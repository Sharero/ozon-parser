"""Collect only product-price markup; never serialize cookies, scripts or form values."""
import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

PRICE_DIAGNOSTIC = r"""() => {
  const clean = text => (text || '').replace(/\s+/g, ' ').trim();
  const markup = node => {
    const clone = node.cloneNode(true);
    clone.querySelectorAll('script, style, iframe, input, textarea, svg, img').forEach(el => el.remove());
    for (const el of [clone, ...clone.querySelectorAll('*')])
      for (const attr of [...el.attributes])
        if (!['data-widget', 'role', 'aria-label', 'aria-hidden'].includes(attr.name)) el.removeAttribute(attr.name);
    return clone.outerHTML.replace(/\s+/g, ' ').slice(0, 6000);
  };
  const nodes = [...document.querySelectorAll('[data-widget]')]
    .filter(node => /price|sale|addtocart/i.test(node.getAttribute('data-widget'))).slice(0, 8);
  const blocks = nodes.map(node => ({widget: node.getAttribute('data-widget'),
    rendered: clean(node.innerText).slice(0, 1200), text: clean(node.textContent).slice(0, 1200), html: markup(node)}));
  const labels = [];
  const main = document.querySelector('main, [role="main"]') || document.body;
  const card = /(?:ozon|озон)\s*[ -]?карт/i;
  for (const node of main.querySelectorAll('span, div, a')) {
    const text = clean(node.innerText);
    if (!card.test(text) || text.length > 100 || [...node.children].some(child => card.test(clean(child.innerText)))) continue;
    const parent = node.parentElement;
    if (parent && clean(parent.innerText).length < 1500) labels.push({label: text,
      parent_text: clean(parent.innerText).slice(0, 1200), html: markup(parent)});
    if (labels.length >= 6) break;
  }
  return {path: location.pathname, title: clean(document.querySelector('h1')?.innerText),
    widget_names: [...new Set([...document.querySelectorAll('[data-widget]')]
      .map(el => el.getAttribute('data-widget')))].slice(0, 80), blocks, labels};
}"""


def save_diagnostic(directory: Path | None, sku: str, diagnostic: dict):
    if directory is None:
        return None
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f'price-{sku}.json'
    path.write_text(json.dumps(diagnostic, ensure_ascii=False, indent=2), encoding='utf-8')
    log.info('Диагностика цены сохранена: %s', path)
    return path
