/**
 * Pure-JS minimal ZIP & EPUB3 builder (zero external dependencies).
 * Uses browser-native CompressionStream('deflate-raw') when available,
 * with automatic fallback to stored (method 0) ZIP entries.
 */
(function (global) {
  'use strict';

  const textEncoder = new TextEncoder();

  // Precompute CRC-32 table
  const CRC_TABLE = (() => {
    const table = new Uint32Array(256);
    for (let i = 0; i < 256; i++) {
      let c = i;
      for (let k = 0; k < 8; k++) {
        c = (c & 1) ? (0xedb88320 ^ (c >>> 1)) : (c >>> 1);
      }
      table[i] = c >>> 0;
    }
    return table;
  })();

  function crc32(bytes) {
    let crc = 0xffffffff;
    for (let i = 0; i < bytes.length; i++) {
      crc = CRC_TABLE[(crc ^ bytes[i]) & 0xff] ^ (crc >>> 8);
    }
    return (crc ^ 0xffffffff) >>> 0;
  }

  async function deflateRaw(bytes) {
    if (typeof CompressionStream === 'undefined') {
      return null;
    }
    try {
      const cs = new CompressionStream('deflate-raw');
      const writer = cs.writable.getWriter();
      writer.write(bytes);
      writer.close();
      const reader = cs.readable.getReader();
      const chunks = [];
      let total = 0;
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        chunks.push(value);
        total += value.byteLength;
      }
      const out = new Uint8Array(total);
      let offset = 0;
      for (const chunk of chunks) {
        out.set(chunk, offset);
        offset += chunk.byteLength;
      }
      return out;
    } catch (_err) {
      return null;
    }
  }

  function toDosDateTime(date) {
    const d = date instanceof Date ? date : new Date();
    const year = Math.max(1980, d.getFullYear());
    const dosTime =
      ((d.getHours() & 0x1f) << 11) |
      ((d.getMinutes() & 0x3f) << 5) |
      ((d.getSeconds() >> 1) & 0x1f);
    const dosDate =
      (((year - 1980) & 0x7f) << 9) |
      (((d.getMonth() + 1) & 0x0f) << 5) |
      (d.getDate() & 0x1f);
    return { dosTime, dosDate };
  }

  async function buildZip(entries) {
    const { dosTime, dosDate } = toDosDateTime(new Date());
    const localParts = [];
    const centralParts = [];
    let offset = 0;

    for (const entry of entries) {
      const nameBytes = textEncoder.encode(entry.name);
      const rawBytes =
        entry.data instanceof Uint8Array
          ? entry.data
          : textEncoder.encode(String(entry.data));
      const crc = crc32(rawBytes);

      let method = 0;
      let payload = rawBytes;
      if (entry.compress !== false && rawBytes.length > 64) {
        const compressed = await deflateRaw(rawBytes);
        if (compressed && compressed.length < rawBytes.length) {
          method = 8;
          payload = compressed;
        }
      }

      // Local file header (30 bytes + filename)
      const localHeader = new ArrayBuffer(30 + nameBytes.length);
      const lv = new DataView(localHeader);
      lv.setUint32(0, 0x04034b50, true);
      lv.setUint16(4, 20, true); // version needed
      lv.setUint16(6, 0x0800, true); // UTF-8 filename flag
      lv.setUint16(8, method, true);
      lv.setUint16(10, dosTime, true);
      lv.setUint16(12, dosDate, true);
      lv.setUint32(14, crc, true);
      lv.setUint32(18, payload.length, true);
      lv.setUint32(22, rawBytes.length, true);
      lv.setUint16(26, nameBytes.length, true);
      lv.setUint16(28, 0, true); // extra length
      new Uint8Array(localHeader, 30).set(nameBytes);

      const localHeaderBytes = new Uint8Array(localHeader);
      localParts.push(localHeaderBytes, payload);

      // Central directory header (46 bytes + filename)
      const centralHeader = new ArrayBuffer(46 + nameBytes.length);
      const cv = new DataView(centralHeader);
      cv.setUint32(0, 0x02014b50, true);
      cv.setUint16(4, 20, true); // version made by
      cv.setUint16(6, 20, true); // version needed
      cv.setUint16(8, 0x0800, true); // UTF-8 flag
      cv.setUint16(10, method, true);
      cv.setUint16(12, dosTime, true);
      cv.setUint16(14, dosDate, true);
      cv.setUint32(16, crc, true);
      cv.setUint32(20, payload.length, true);
      cv.setUint32(24, rawBytes.length, true);
      cv.setUint16(28, nameBytes.length, true);
      cv.setUint16(30, 0, true); // extra
      cv.setUint16(32, 0, true); // comment
      cv.setUint16(34, 0, true); // disk start
      cv.setUint16(36, 0, true); // internal attrs
      cv.setUint32(38, 0, true); // external attrs
      cv.setUint32(42, offset, true);
      new Uint8Array(centralHeader, 46).set(nameBytes);

      centralParts.push(new Uint8Array(centralHeader));
      offset += localHeaderBytes.length + payload.length;
    }

    const centralOffset = offset;
    let centralSize = 0;
    for (const part of centralParts) {
      centralSize += part.length;
    }

    // End of central directory (22 bytes)
    const eocd = new ArrayBuffer(22);
    const ev = new DataView(eocd);
    ev.setUint32(0, 0x06054b50, true);
    ev.setUint16(4, 0, true);
    ev.setUint16(6, 0, true);
    ev.setUint16(8, entries.length, true);
    ev.setUint16(10, entries.length, true);
    ev.setUint32(12, centralSize, true);
    ev.setUint32(16, centralOffset, true);
    ev.setUint16(20, 0, true);

    const totalSize = centralOffset + centralSize + 22;
    const result = new Uint8Array(totalSize);
    let pos = 0;
    for (const part of localParts) {
      result.set(part, pos);
      pos += part.length;
    }
    for (const part of centralParts) {
      result.set(part, pos);
      pos += part.length;
    }
    result.set(new Uint8Array(eocd), pos);
    return result;
  }

  function escapeXml(str) {
    return String(str || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function sanitizeFilename(title, fallback) {
    const base = String(title || fallback || 'article')
      .replace(/[<>:"/\\|?*\x00-\x1f]/g, '_')
      .replace(/\s+/g, ' ')
      .trim()
      .slice(0, 80);
    return (base || 'article') + '.epub';
  }

  /**
   * Ensure an HTML body string is valid XHTML5 for EPUB3.
   * Converts void HTML tags to self-closing and strips dangerous tags.
   */
  function normalizeToXhtml(html) {
    let s = String(html || '');
    // Strip script, style, iframe, video, audio, object, embed, form
    s = s.replace(/<(script|style|noscript|iframe|video|audio|object|embed|svg|canvas)[\s\S]*?<\/\1>/gi, '');
    s = s.replace(/<(script|style|iframe|video|audio|object|embed)[^>]*\/?>/gi, '');
    // Replace HTML named entities that XML doesn't know with numeric entities
    const entityMap = {
      nbsp: '&#160;',
      copy: '&#169;',
      reg: '&#174;',
      trade: '&#8482;',
      mdash: '&#8212;',
      ndash: '&#8211;',
      hellip: '&#8230;',
      lsquo: '&#8216;',
      rsquo: '&#8217;',
      ldquo: '&#8220;',
      rdquo: '&#8221;',
      laquo: '&#171;',
      raquo: '&#187;',
      bull: '&#8226;',
      middot: '&#183;',
      times: '&#215;',
      divide: '&#247;',
    };
    s = s.replace(/&([a-zA-Z]+);/g, (m, name) => {
      if (name === 'amp' || name === 'lt' || name === 'gt' || name === 'quot' || name === 'apos') {
        return m;
      }
      return entityMap[name] || ' ';
    });
    // Self-close void elements
    const voidTags = ['br', 'hr', 'img', 'input', 'meta', 'link', 'source', 'wbr'];
    for (const tag of voidTags) {
      const re = new RegExp('<' + tag + '(\\s[^>]*?)?(?<!/)>', 'gi');
      s = s.replace(re, '<' + tag + '$1/>');
    }
    // Escape stray ampersands not part of an entity
    s = s.replace(/&(?!(?:#\d+|#x[\da-fA-F]+|amp|lt|gt|quot|apos);)/g, '&amp;');
    return s;
  }

  function guessImageExtension(mimeType, urlOrName) {
    const mime = String(mimeType || '').toLowerCase();
    if (mime.includes('jpeg') || mime.includes('jpg')) return { ext: 'jpg', mime: 'image/jpeg' };
    if (mime.includes('png')) return { ext: 'png', mime: 'image/png' };
    if (mime.includes('gif')) return { ext: 'gif', mime: 'image/gif' };
    if (mime.includes('webp')) return { ext: 'webp', mime: 'image/webp' };
    if (mime.includes('svg')) return { ext: 'svg', mime: 'image/svg+xml' };
    const clean = String(urlOrName || '').split('?')[0].split('#')[0].toLowerCase();
    if (clean.endsWith('.png')) return { ext: 'png', mime: 'image/png' };
    if (clean.endsWith('.gif')) return { ext: 'gif', mime: 'image/gif' };
    if (clean.endsWith('.webp')) return { ext: 'webp', mime: 'image/webp' };
    if (clean.endsWith('.svg')) return { ext: 'svg', mime: 'image/svg+xml' };
    return { ext: 'jpg', mime: 'image/jpeg' };
  }

  const DEFAULT_CSS = `
body {
  font-family: "Noto Serif CJK SC", "Songti SC", Georgia, serif;
  line-height: 1.75;
  margin: 1em 1.2em;
  color: #111;
}
h1, h2, h3, h4 {
  line-height: 1.35;
  margin-top: 1.4em;
  margin-bottom: 0.5em;
  font-weight: bold;
}
h1 { font-size: 1.55em; }
h2 { font-size: 1.3em; }
h3 { font-size: 1.12em; }
.meta {
  color: #555;
  font-size: 0.9em;
  margin-bottom: 1.4em;
  padding-bottom: 0.8em;
  border-bottom: 1px solid #ddd;
}
p {
  margin: 0.75em 0;
  text-align: left;
}
blockquote {
  margin: 1em 0;
  padding: 0.4em 1em;
  border-left: 3px solid #888;
  color: #444;
  background: #f7f7f7;
}
pre, code {
  font-family: monospace;
  font-size: 0.88em;
  background: #f3f3f3;
}
pre {
  padding: 0.8em;
  overflow-x: auto;
  white-space: pre-wrap;
  word-break: break-word;
  border-radius: 4px;
}
code {
  padding: 0.1em 0.3em;
  border-radius: 3px;
}
img {
  max-width: 100%;
  height: auto;
  display: block;
  margin: 0.9em auto;
}
figure {
  margin: 1em 0;
  text-align: center;
}
figcaption {
  font-size: 0.85em;
  color: #666;
  margin-top: 0.3em;
}
table {
  border-collapse: collapse;
  width: 100%;
  margin: 1em 0;
  font-size: 0.9em;
}
th, td {
  border: 1px solid #ccc;
  padding: 0.4em 0.6em;
  text-align: left;
}
th {
  background: #f0f0f0;
}
.video-placeholder {
  border: 1px dashed #999;
  padding: 0.8em;
  margin: 1em 0;
  text-align: center;
  background: #fafafa;
}
a {
  color: #1a5fb4;
  word-break: break-all;
}
`.trim();

  /**
   * Build an EPUB3 file as Uint8Array.
   * @param {Object} opts
   * @param {string} opts.title
   * @param {string} [opts.author]
   * @param {string} [opts.sourceUrl]
   * @param {string} [opts.language]
   * @param {string} [opts.coverId] - id of cover image in images array
   * @param {string} opts.bodyHtml - XHTML-compatible body HTML (referencing images/img_0.jpg etc.)
   * @param {Array<{id: string, filename: string, mimeType: string, data: Uint8Array}>} [opts.images]
   */
  async function buildEpub(opts) {
    const title = String(opts.title || 'Untitled Article').trim();
    const author = String(opts.author || 'Unknown').trim();
    const sourceUrl = String(opts.sourceUrl || '').trim();
    const language = String(opts.language || 'zh-CN').trim();
    const images = Array.isArray(opts.images) ? opts.images : [];
    const bookId = 'urn:uuid:' + generateUuid();
    const modified = new Date().toISOString().replace(/\.\d{3}Z$/, 'Z');

    const safeBody = normalizeToXhtml(opts.bodyHtml || '<p>(Empty)</p>');

    const metaParts = [
      author ? `<span>作者：${escapeXml(author)}</span>` : '',
      sourceUrl
        ? `<span>来源：<a href="${escapeXml(sourceUrl)}">${escapeXml(sourceUrl)}</a></span>`
        : '',
    ].filter(Boolean);

    const headerHtml = `<h1>${escapeXml(title)}</h1>\n` +
      (metaParts.length ? `<div class="meta">${metaParts.join(' &#183; ')}</div>\n` : '');

    const chapterXhtml = `<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="${escapeXml(language)}">
<head>
  <meta charset="utf-8"/>
  <title>${escapeXml(title)}</title>
  <link rel="stylesheet" type="text/css" href="style.css"/>
</head>
<body>
${headerHtml}
${safeBody}
</body>
</html>`;

    const navXhtml = `<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="${escapeXml(language)}">
<head>
  <meta charset="utf-8"/>
  <title>${escapeXml(title)}</title>
</head>
<body>
  <nav epub:type="toc" id="toc">
    <h1>目录</h1>
    <ol>
      <li><a href="chapter1.xhtml">${escapeXml(title)}</a></li>
    </ol>
  </nav>
</body>
</html>`;

    const imageManifestItems = images
      .map((img) => {
        const props = opts.coverId && img.id === opts.coverId ? ' properties="cover-image"' : '';
        return `    <item id="${escapeXml(img.id)}" href="images/${escapeXml(img.filename)}" media-type="${escapeXml(img.mimeType)}"${props}/>`;
      })
      .join('\n');

    const sourceMeta = sourceUrl
      ? `    <dc:source>${escapeXml(sourceUrl)}</dc:source>\n`
      : '';

    const contentOpf = `<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">${escapeXml(bookId)}</dc:identifier>
    <dc:title>${escapeXml(title)}</dc:title>
    <dc:language>${escapeXml(language)}</dc:language>
    <dc:creator>${escapeXml(author)}</dc:creator>
${sourceMeta}    <meta property="dcterms:modified">${escapeXml(modified)}</meta>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="css" href="style.css" media-type="text/css"/>
    <item id="chapter1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>
${imageManifestItems}
  </manifest>
  <spine>
    <itemref idref="chapter1"/>
  </spine>
</package>`;

    const containerXml = `<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>`;

    const zipEntries = [
      { name: 'mimetype', data: 'application/epub+zip', compress: false },
      { name: 'META-INF/container.xml', data: containerXml, compress: true },
      { name: 'OEBPS/content.opf', data: contentOpf, compress: true },
      { name: 'OEBPS/nav.xhtml', data: navXhtml, compress: true },
      { name: 'OEBPS/style.css', data: DEFAULT_CSS, compress: true },
      { name: 'OEBPS/chapter1.xhtml', data: chapterXhtml, compress: true },
    ];

    for (const img of images) {
      zipEntries.push({
        name: 'OEBPS/images/' + img.filename,
        data: img.data,
        compress: false,
      });
    }

    return buildZip(zipEntries);
  }

  function generateUuid() {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
      return crypto.randomUUID();
    }
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
      const r = (Math.random() * 16) | 0;
      const v = c === 'x' ? r : (r & 0x3) | 0x8;
      return v.toString(16);
    });
  }

  function uint8ArrayToBase64(bytes) {
    const chunkSize = 0x8000;
    let binary = '';
    for (let i = 0; i < bytes.length; i += chunkSize) {
      const sub = bytes.subarray(i, i + chunkSize);
      binary += String.fromCharCode.apply(null, sub);
    }
    return btoa(binary);
  }

  global.EpubBuilder = {
    buildZip,
    buildEpub,
    escapeXml,
    normalizeToXhtml,
    sanitizeFilename,
    guessImageExtension,
    uint8ArrayToBase64,
  };
})(typeof globalThis !== 'undefined' ? globalThis : window);
