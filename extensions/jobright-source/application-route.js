/* Read-only rendered job document. Forms and unrelated executable code are omitted. */
var ApplicationRoutePage = (() => {
  function capture(doc, url) {
    const copy = doc.documentElement.cloneNode(true);
    for (const node of copy.querySelectorAll('input,textarea,select,style,script:not([type="application/ld+json"])')) node.parentNode.removeChild(node);
    for (const node of copy.querySelectorAll('*')) {
      for (const attr of [...node.attributes]) {
        if (/^on/i.test(attr.name) || ['value','checked','selected'].includes(attr.name)) node.removeAttribute(attr.name);
      }
    }
    const html = copy.outerHTML;
    if (html.length > 512000) throw new Error('Application page exceeds snapshot size limit');
    // Capture from the sanitized clone too: select/option text can contain user data.
    const sanitized = doc.implementation.createHTMLDocument(doc.title || '');
    sanitized.replaceChild(sanitized.importNode(copy, true), sanitized.documentElement);
    return {url, html, page_snapshot: JobPageSnapshot.capture(sanitized, url)};
  }
  return {capture};
})();
if (typeof module !== 'undefined') module.exports = ApplicationRoutePage;
