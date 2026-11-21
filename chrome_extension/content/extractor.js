/**
 * Content-script article extractor for X Articles and general Web/Blog articles.
 * Injected on-demand via chrome.scripting.executeScript.
 */
(function () {
  'use strict';

  function isXDomain(hostname) {
    const h = String(hostname || '').toLowerCase();
    return h === 'x.com' || h.endsWith('.x.com') || h === 'twitter.com' || h.endsWith('.twitter.com');
  }

  function escapeHtml(str) {
    return String(str || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function resolveUrl(rawUrl) {
    if (!rawUrl) return '';
    try {
      return new URL(rawUrl, location.href).href;
    } catch (_e) {
      return '';
    }
  }

  function getBestImgSrc(img) {
    if (!img) return '';
    const candidates = [
      img.getAttribute('data-src'),
      img.getAttribute('data-original'),
      img.getAttribute('data-lazy-src'),
      img.getAttribute('data-actualsrc'),
      img.currentSrc,
      img.getAttribute('src'),
    ];
    for (const c of candidates) {
      if (c && !c.startsWith('data:image/svg+xml')) {
        return resolveUrl(c);
      }
    }
    return '';
  }

  function upgradeTwitterImageUrl(url) {
    try {
      const u = new URL(url);
      if (u.hostname.includes('twimg.com') && u.searchParams.has('name')) {
        u.searchParams.set('name', 'large');
        return u.toString();
      }
    } catch (_e) {
      // ignore
    }
    return url;
  }

  function replaceVideosAndCollectImages(rootEl, sourceUrl, imageRefs, isX) {
    // Replace video player wrappers on X or generic <video> elements
    const videoSelectors = isX
      ? '[data-testid="videoPlayer"], [data-testid="videoComponent"], video'
      : 'video';

    const videos = Array.from(rootEl.querySelectorAll(videoSelectors));
    const processed = new Set();

    for (const vid of videos) {
      if (processed.has(vid)) continue;
      // Avoid double-processing nested <video> inside [data-testid="videoPlayer"]
      let parent = vid.parentElement;
      let skip = false;
      while (parent && parent !== rootEl) {
        if (processed.has(parent)) {
          skip = true;
          break;
        }
        parent = parent.parentElement;
      }
      if (skip) continue;
      processed.add(vid);

      const innerVideo = vid.tagName === 'VIDEO' ? vid : vid.querySelector('video');
      const posterUrl =
        (innerVideo && resolveUrl(innerVideo.getAttribute('poster'))) ||
        getBestImgSrc(vid.querySelector('img'));

      const wrapper = document.createElement('div');
      wrapper.className = 'video-placeholder';

      if (posterUrl) {
        const finalPoster = isX ? upgradeTwitterImageUrl(posterUrl) : posterUrl;
        const idx = imageRefs.length;
        imageRefs.push({ token: `@@IMG_TOKEN_${idx}@@`, url: finalPoster, alt: '视频封面' });
        const imgEl = document.createElement('img');
        imgEl.setAttribute('src', `@@IMG_TOKEN_${idx}@@`);
        imgEl.setAttribute('alt', '视频封面');
        wrapper.appendChild(imgEl);
      }

      const linkP = document.createElement('p');
      linkP.innerHTML = `🎬 视频内容（点击查看原文）：<a href="${escapeHtml(sourceUrl)}">${escapeHtml(sourceUrl)}</a>`;
      wrapper.appendChild(linkP);

      if (vid.parentNode) {
        vid.parentNode.replaceChild(wrapper, vid);
      }
    }

    // Replace embedded video iframes (YouTube, Vimeo, Bilibili)
    const iframes = Array.from(rootEl.querySelectorAll('iframe'));
    for (const frame of iframes) {
      const src = resolveUrl(frame.getAttribute('src') || frame.getAttribute('data-src'));
      if (/youtube\.com|youtu\.be|vimeo\.com|player\.bilibili\.com/i.test(src)) {
        const wrapper = document.createElement('div');
        wrapper.className = 'video-placeholder';
        wrapper.innerHTML = `<p>🎬 嵌入视频：<a href="${escapeHtml(src || sourceUrl)}">${escapeHtml(src || sourceUrl)}</a></p>`;
        frame.parentNode.replaceChild(wrapper, frame);
      } else {
        frame.remove();
      }
    }

    // Remove noise elements
    const noiseSelectors = [
      'script', 'style', 'noscript', 'svg', 'canvas', 'button', 'input', 'form',
      '[aria-hidden="true"]', '.advertisement', '.ad-container', '.share-buttons',
      '.social-share', '.related-posts', '.comments', '#comments',
    ];
    for (const sel of noiseSelectors) {
      for (const el of rootEl.querySelectorAll(sel)) {
        el.remove();
      }
    }

    // Collect images
    const imgs = Array.from(rootEl.querySelectorAll('img'));
    for (const img of imgs) {
      const existingSrc = img.getAttribute('src') || '';
      if (existingSrc.startsWith('@@IMG_TOKEN_')) {
        continue;
      }
      const w = Number(img.getAttribute('width') || 0);
      const h = Number(img.getAttribute('height') || 0);
      if ((w > 0 && w <= 2) || (h > 0 && h <= 2)) {
        img.remove();
        continue;
      }
      let src = getBestImgSrc(img);
      if (!src || src.startsWith('data:image/svg+xml')) {
        img.remove();
        continue;
      }
      if (isX) {
        src = upgradeTwitterImageUrl(src);
      }
      const alt = img.getAttribute('alt') || '';
      const idx = imageRefs.length;
      const token = `@@IMG_TOKEN_${idx}@@`;
      imageRefs.push({ token, url: src, alt });
      img.setAttribute('src', token);
      img.setAttribute('alt', alt);
      // Clean unnecessary attributes
      for (const attr of Array.from(img.attributes)) {
        if (attr.name !== 'src' && attr.name !== 'alt') {
          img.removeAttribute(attr.name);
        }
      }
    }

    // Resolve relative links and strip event handlers / inline styles
    for (const el of rootEl.querySelectorAll('*')) {
      for (const attr of Array.from(el.attributes)) {
        const name = attr.name.toLowerCase();
        if (name.startsWith('on') || name === 'style' || name === 'class' || name === 'id' || name.startsWith('data-')) {
          if (!(el.tagName === 'DIV' && el.className === 'video-placeholder')) {
            el.removeAttribute(attr.name);
          }
        }
      }
      if (el.tagName === 'A') {
        const href = resolveUrl(el.getAttribute('href'));
        if (href) {
          el.setAttribute('href', href);
        } else {
          el.removeAttribute('href');
        }
      }
    }
  }

  function extractXArticle() {
    const readView = document.querySelector('[data-testid="twitterArticleReadView"]');
    const richText = document.querySelector('[data-testid="twitterArticleRichTextView"]');
    const titleEl = document.querySelector('[data-testid="twitter-article-title"]');
    const isArticleUrl = /\/i\/articles\//i.test(location.pathname);

    if (!readView && !richText && !titleEl && !isArticleUrl) {
      return {
        mode: 'x',
        ok: false,
        errorCode: 'NOT_X_ARTICLE',
        message: '当前页面不是 X 长文章（X Articles）。仅支持 X 长文章，不支持普通推文或推文串。',
      };
    }

    const container = richText || readView;
    if (!container) {
      return {
        mode: 'x',
        ok: false,
        errorCode: 'X_ARTICLE_EMPTY',
        message: '检测到 X 文章页面，但正文尚未加载完成，请滚动加载后重试。',
      };
    }

    const title =
      (titleEl && titleEl.innerText.trim()) ||
      (readView && readView.querySelector('h1') && readView.querySelector('h1').innerText.trim()) ||
      document.title.replace(/\s*\/\s*X$/, '').trim() ||
      'X Article';

    let author = '';
    const userNameEl =
      (readView && readView.querySelector('[data-testid="User-Name"]')) ||
      document.querySelector('[data-testid="User-Name"]');
    if (userNameEl) {
      author = userNameEl.innerText.replace(/\s+/g, ' ').trim();
    }
    if (!author) {
      const m = location.pathname.match(/^\/([^/]+)\//);
      if (m && m[1] !== 'i') author = '@' + m[1];
    }

    const sourceUrl = location.href;
    const imageRefs = [];

    // Cover image if outside richText
    let coverHtml = '';
    const coverImg = document.querySelector('[data-testid="article-cover-image"] img');
    if (coverImg && (!richText || !richText.contains(coverImg))) {
      let coverUrl = getBestImgSrc(coverImg);
      if (coverUrl) {
        coverUrl = upgradeTwitterImageUrl(coverUrl);
        const idx = imageRefs.length;
        const token = `@@IMG_TOKEN_${idx}@@`;
        imageRefs.push({ token, url: coverUrl, alt: '封面图', isCover: true });
        coverHtml = `<figure><img src="${token}" alt="封面图"/></figure>\n`;
      }
    }

    const clone = container.cloneNode(true);
    // Remove duplicate title if it's inside readView clone
    if (titleEl && !richText) {
      const dupTitle = clone.querySelector('[data-testid="twitter-article-title"]');
      if (dupTitle) dupTitle.remove();
    }

    replaceVideosAndCollectImages(clone, sourceUrl, imageRefs, true);

    return {
      mode: 'x',
      ok: true,
      title,
      author: author || 'X Author',
      sourceUrl,
      bodyHtml: coverHtml + clone.innerHTML,
      imageRefs,
    };
  }

  function scoreElement(el) {
    const text = (el.innerText || '').trim();
    if (text.length < 200) return -1;
    const pCount = el.querySelectorAll('p').length;
    const links = el.querySelectorAll('a');
    let linkTextLen = 0;
    for (const a of links) {
      linkTextLen += (a.innerText || '').length;
    }
    const linkDensity = text.length > 0 ? linkTextLen / text.length : 1;
    if (linkDensity > 0.55) return -1;
    let score = text.length * (1 - linkDensity) + pCount * 80;
    const tag = el.tagName.toLowerCase();
    if (tag === 'article') score *= 1.5;
    if (tag === 'main') score *= 1.25;
    const cls = (el.className || '') + ' ' + (el.id || '');
    if (/article|post|entry|content|markdown|story|prose/i.test(cls)) score *= 1.3;
    if (/sidebar|nav|footer|header|comment|menu|related|recommend/i.test(cls)) score *= 0.25;
    return score;
  }

  function extractWebArticle() {
    const ogTitle = document.querySelector('meta[property="og:title"]');
    const h1 = document.querySelector('article h1, main h1, h1');
    const title =
      (h1 && h1.innerText.trim()) ||
      (ogTitle && ogTitle.getAttribute('content') && ogTitle.getAttribute('content').trim()) ||
      document.title.trim() ||
      'Web Article';

    const authorMeta =
      document.querySelector('meta[name="author"]') ||
      document.querySelector('meta[property="article:author"]') ||
      document.querySelector('[rel="author"]') ||
      document.querySelector('.author, .byline');
    const author =
      (authorMeta &&
        (authorMeta.getAttribute('content') || authorMeta.innerText || '').trim()) ||
      location.hostname;

    const canonical = document.querySelector('link[rel="canonical"]');
    const sourceUrl = (canonical && resolveUrl(canonical.getAttribute('href'))) || location.href;

    const prioritySelectors = [
      'article',
      'main',
      '[role="main"]',
      '.post-content',
      '.article-content',
      '.entry-content',
      '.markdown-body',
      '#js_content',
      '#content',
    ];

    let bestEl = null;
    let bestScore = -1;

    for (const sel of prioritySelectors) {
      for (const el of document.querySelectorAll(sel)) {
        const sc = scoreElement(el);
        if (sc > bestScore) {
          bestScore = sc;
          bestEl = el;
        }
      }
    }

    if (!bestEl) {
      for (const el of document.querySelectorAll('div, section')) {
        const sc = scoreElement(el);
        if (sc > bestScore) {
          bestScore = sc;
          bestEl = el;
        }
      }
    }

    if (!bestEl) {
      bestEl = document.body;
    }

    const clone = bestEl.cloneNode(true);
    for (const sel of ['nav', 'aside', 'footer', 'header']) {
      for (const el of clone.querySelectorAll(sel)) {
        if ((el.innerText || '').length < 400) {
          el.remove();
        }
      }
    }
    // Remove duplicate H1 if it matches title
    const cloneH1 = clone.querySelector('h1');
    if (cloneH1 && cloneH1.innerText.trim() === title) {
      cloneH1.remove();
    }

    const imageRefs = [];
    replaceVideosAndCollectImages(clone, sourceUrl, imageRefs, false);

    return {
      mode: 'web',
      ok: true,
      title,
      author,
      sourceUrl,
      bodyHtml: clone.innerHTML,
      imageRefs,
    };
  }

  window.__kindleArticleExtractor = function () {
    if (isXDomain(location.hostname)) {
      return extractXArticle();
    }
    return extractWebArticle();
  };
})();
