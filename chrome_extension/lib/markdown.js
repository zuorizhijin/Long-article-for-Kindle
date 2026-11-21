/**
 * Pure-JS Markdown + Obsidian syntax to XHTML converter.
 */
(function (global) {
  'use strict';

  function escapeHtml(str) {
    return String(str || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function parseFrontmatter(md) {
    const text = String(md || '').replace(/\r\n/g, '\n');
    const match = text.match(/^---\n([\s\S]*?)\n---\n?([\s\S]*)$/);
    if (!match) {
      return { meta: {}, body: text };
    }
    const meta = {};
    const lines = match[1].split('\n');
    for (const line of lines) {
      const m = line.match(/^([A-Za-z0-9_-]+)\s*:\s*(.*)$/);
      if (m) {
        let val = m[2].trim();
        if (
          (val.startsWith('"') && val.endsWith('"')) ||
          (val.startsWith("'") && val.endsWith("'"))
        ) {
          val = val.slice(1, -1);
        }
        meta[m[1].toLowerCase()] = val;
      }
    }
    return { meta, body: match[2] };
  }

  function convertMarkdown(mdText, fallbackTitle) {
    const { meta, body } = parseFrontmatter(mdText);
    const imageRefs = [];
    const codeBlocks = [];

    // 1. Extract fenced code blocks first so their contents aren't parsed as Markdown
    let working = body.replace(/^(```|~~~)([^\n]*)\n([\s\S]*?)\n\1\s*$/gm, (_m, _fence, lang, code) => {
      const idx = codeBlocks.length;
      const langAttr = lang.trim() ? ` class="language-${escapeHtml(lang.trim())}"` : '';
      codeBlocks.push(`<pre><code${langAttr}>${escapeHtml(code)}</code></pre>`);
      return `\n@@CODEBLOCK_${idx}@@\n`;
    });

    function registerImage(rawPath, alt) {
      const cleanPath = String(rawPath || '').trim();
      const idx = imageRefs.length;
      const token = `@@IMG_TOKEN_${idx}@@`;
      imageRefs.push({ token, rawPath: cleanPath, alt: String(alt || '').trim() });
      return token;
    }

    function renderInline(text) {
      const inlineCodes = [];
      let s = String(text || '');

      // Protect inline code
      s = s.replace(/`([^`]+)`/g, (_m, code) => {
        const idx = inlineCodes.length;
        inlineCodes.push(`<code>${escapeHtml(code)}</code>`);
        return `@@INLINECODE_${idx}@@`;
      });

      // Extract Obsidian image embeds: ![[path|alt or size]]
      s = s.replace(/!\[\[([^\]]+)\]\]/g, (_m, inner) => {
        const parts = inner.split('|');
        const rawPath = parts[0].trim();
        let alt = '';
        if (parts.length > 1) {
          const second = parts.slice(1).join('|').trim();
          if (!/^\d+(x\d+)?$/.test(second)) {
            alt = second;
          }
        }
        return registerImage(rawPath, alt);
      });

      // Extract standard Markdown images: ![alt](url) or ![alt](<url>)
      s = s.replace(/!\[([^\]]*)\]\((?:<([^>]+)>|([^)\s]+)(?:\s+"[^"]*")?)\)/g, (_m, alt, anglePath, normalPath) => {
        return registerImage(anglePath || normalPath, alt);
      });

      // Escape remaining HTML characters
      s = escapeHtml(s);

      // Links: [text](url)
      s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+|[^)\s]+)\)/g, (_m, label, url) => {
        return `<a href="${url}">${label}</a>`;
      });

      // Obsidian wikilinks: [[note|alias]] or [[note]]
      s = s.replace(/\[\[([^\]]+)\]\]/g, (_m, inner) => {
        const parts = inner.split('|');
        const display = parts.length > 1 ? parts[1].trim() : parts[0].trim();
        return `<strong>${display}</strong>`;
      });

      // Bold & Italic & Strikethrough
      s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
      s = s.replace(/__([^_]+)__/g, '<strong>$1</strong>');
      s = s.replace(/\*([^*]+)\*/g, '<em>$1</em>');
      s = s.replace(/_([^_]+)_/g, '<em>$1</em>');
      s = s.replace(/~~([^~]+)~~/g, '<del>$1</del>');

      // Restore inline code
      s = s.replace(/@@INLINECODE_(\d+)@@/g, (_m, idx) => inlineCodes[Number(idx)] || '');

      return s;
    }

    const lines = working.split('\n');
    const out = [];
    let i = 0;
    let detectedTitle = meta.title || '';

    while (i < lines.length) {
      const line = lines[i];
      const trimmed = line.trim();

      if (!trimmed) {
        i++;
        continue;
      }

      // Code block placeholder
      const cbMatch = trimmed.match(/^@@CODEBLOCK_(\d+)@@$/);
      if (cbMatch) {
        out.push(codeBlocks[Number(cbMatch[1])] || '');
        i++;
        continue;
      }

      // Heading
      const hMatch = trimmed.match(/^(#{1,6})\s+(.*)$/);
      if (hMatch) {
        const level = hMatch[1].length;
        const headingText = hMatch[2].replace(/\s+#+\s*$/, '').trim();
        if (level === 1 && !detectedTitle) {
          detectedTitle = headingText;
        }
        out.push(`<h${level}>${renderInline(headingText)}</h${level}>`);
        i++;
        continue;
      }

      // Horizontal rule
      if (/^(?:---|\*\*\*|___)\s*$/.test(trimmed)) {
        out.push('<hr/>');
        i++;
        continue;
      }

      // Blockquote
      if (trimmed.startsWith('>')) {
        const quoteLines = [];
        while (i < lines.length && lines[i].trim().startsWith('>')) {
          quoteLines.push(lines[i].trim().replace(/^>\s?/, ''));
          i++;
        }
        out.push(`<blockquote><p>${quoteLines.map(renderInline).join('<br/>')}</p></blockquote>`);
        continue;
      }

      // Table (header | row + separator |---|)
      if (
        trimmed.includes('|') &&
        i + 1 < lines.length &&
        /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$/.test(lines[i + 1])
      ) {
        const parseRow = (rowLine) =>
          rowLine
            .trim()
            .replace(/^\|/, '')
            .replace(/\|$/, '')
            .split('|')
            .map((cell) => cell.trim());

        const headers = parseRow(lines[i]);
        i += 2; // skip header and separator
        const bodyRows = [];
        while (i < lines.length && lines[i].trim().includes('|') && lines[i].trim() !== '') {
          bodyRows.push(parseRow(lines[i]));
          i++;
        }
        let tableHtml = '<table><thead><tr>';
        for (const h of headers) {
          tableHtml += `<th>${renderInline(h)}</th>`;
        }
        tableHtml += '</tr></thead><tbody>';
        for (const r of bodyRows) {
          tableHtml += '<tr>';
          for (let c = 0; c < headers.length; c++) {
            tableHtml += `<td>${renderInline(r[c] || '')}</td>`;
          }
          tableHtml += '</tr>';
        }
        tableHtml += '</tbody></table>';
        out.push(tableHtml);
        continue;
      }

      // Unordered list
      if (/^\s*[-*+]\s+/.test(line)) {
        const items = [];
        while (i < lines.length && /^\s*[-*+]\s+/.test(lines[i])) {
          let itemText = lines[i].replace(/^\s*[-*+]\s+/, '');
          itemText = itemText.replace(/^\[ \]\s+/, '☐ ').replace(/^\[[xX]\]\s+/, '☑ ');
          items.push(`<li>${renderInline(itemText)}</li>`);
          i++;
        }
        out.push(`<ul>${items.join('')}</ul>`);
        continue;
      }

      // Ordered list
      if (/^\s*\d+\.\s+/.test(line)) {
        const items = [];
        while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) {
          const itemText = lines[i].replace(/^\s*\d+\.\s+/, '');
          items.push(`<li>${renderInline(itemText)}</li>`);
          i++;
        }
        out.push(`<ol>${items.join('')}</ol>`);
        continue;
      }

      // Paragraph: collect contiguous non-special lines
      const paraLines = [];
      while (i < lines.length) {
        const cur = lines[i];
        const curTrim = cur.trim();
        if (
          !curTrim ||
          /^@@CODEBLOCK_\d+@@$/.test(curTrim) ||
          /^(#{1,6})\s+/.test(curTrim) ||
          /^(?:---|\*\*\*|___)\s*$/.test(curTrim) ||
          curTrim.startsWith('>') ||
          /^\s*[-*+]\s+/.test(cur) ||
          /^\s*\d+\.\s+/.test(cur)
        ) {
          break;
        }
        paraLines.push(curTrim);
        i++;
      }
      if (paraLines.length > 0) {
        out.push(`<p>${paraLines.map(renderInline).join('<br/>')}</p>`);
      }
    }

    const title = detectedTitle || fallbackTitle || 'Obsidian Note';
    const author = meta.author || 'Obsidian';
    return {
      title,
      author,
      html: out.join('\n'),
      imageRefs,
    };
  }

  global.MarkdownConverter = {
    parseFrontmatter,
    convertMarkdown,
  };
})(typeof globalThis !== 'undefined' ? globalThis : window);
