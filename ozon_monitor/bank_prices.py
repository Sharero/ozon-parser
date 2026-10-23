"""Resolve explicit Ozon Bank prices from the bank offer popup, never the group minimum."""
import re
from .models import normalized, rubles

OZON_LABEL = r'(?:с\s+)?(?:ozon|озон)[ -]?(?:банк(?:ом|а)?|карт(?:ой|а|ы))'


def select_ozon_price(details, group_price=None):
    prices = set()
    for row in details.get('rows', []):
        if not isinstance(row, dict) or not re.fullmatch(OZON_LABEL, normalized(row.get('label', ''))):
            continue
        price = rubles(row.get('price', ''))
        if price is not None and price > 0:
            prices.add(price)
    if len(prices) > 1:
        raise RuntimeError('Список банков содержит противоречивые цены Ozon Банка')
    if prices:
        return next(iter(prices))
    membership = details.get('membership') or {}
    if (membership.get('source') == 'opened_bank_group_cell_list'
            and re.fullmatch(OZON_LABEL, normalized(membership.get('bank', '')))):
        price = rubles(group_price) if isinstance(group_price, str) else None
        return price if price is not None and price > 0 else None
    return None


OPEN_BANK_DETAILS = r"""() => {
    const root = document.querySelector('[data-widget="webPrice"]');
    if (!root) return false;
    const label = [...root.querySelectorAll('*')].find(el => /^с банками$/i.test((el.innerText || '').trim()));
    const button = label?.closest('button, [role="button"], a');
    if (!button) return false;
    window.__ozonMonitorBankNodes = new WeakSet(document.querySelectorAll('*'));
    button.click();
    return true;
}"""

READ_BANK_DETAILS = r"""() => {
    const clean = text => (text || '').replace(/\s+/g, ' ').trim();
    const visible = node => node.getClientRects().length && getComputedStyle(node).visibility !== 'hidden';
    const before = window.__ozonMonitorBankNodes;
    const dialogs = [...document.querySelectorAll('[role="dialog"], [aria-modal="true"], [data-widget="modalLayout"]')].filter(visible);
    const bank = /^(?:с\s+)?(?:ozon|озон)[ -]?(?:банк(?:ом|а)?|карт(?:ой|а|ы))$/i;
    const rows = [], snippets = [];
    let membership = null;
    const html = node => {
        const clone = node.cloneNode(true);
        clone.querySelectorAll('script,style,iframe,input,textarea,svg,img').forEach(el => el.remove());
        for (const el of [clone, ...clone.querySelectorAll('*')]) for (const attr of [...el.attributes])
            if (!['role', 'aria-label', 'aria-modal', 'data-widget'].includes(attr.name)) el.removeAttribute(attr.name);
        return clone.outerHTML.replace(/\s+/g, ' ').slice(0, 6000);
    };
    for (const dialog of dialogs.slice(0, 3)) snippets.push({text: clean(dialog.innerText).slice(0, 4000), html: html(dialog)});
    for (const el of document.querySelectorAll('span,div,p,a,img')) {
        if (!visible(el)) continue;
        if (!dialogs.some(dialog => dialog.contains(el)) && (!before || before.has(el))) continue;
        const label = clean(el.tagName === 'IMG' ? el.alt : el.innerText);
        if (!bank.test(label) || [...el.children].some(child => bank.test(clean(child.innerText)))) continue;
        let node = el.parentElement;
        for (let depth = 0; node && depth < 7; depth++, node = node.parentElement) {
            if (node === document.body || (before?.has(node) && !dialogs.some(dialog => dialog.contains(node)))) break;
            const text = clean(node.innerText);
            if (text.length > 2000) break;
            const amounts = text.match(/\d[\d\s]*(?:[.,]\d{1,2})?\s*₽/g) || [];
            if (amounts.length === 1) { rows.push({label, price: amounts[0], text}); break; }
            if (amounts.length > 1) break;
        }
        const dialog = dialogs.find(item => item.contains(el));
        if (before && dialog && el.closest('[data-widget="cellList"]') && !/₽/.test(dialog.innerText))
            membership = {source: 'opened_bank_group_cell_list', bank: label};
        const parent = el.parentElement;
        if (parent && snippets.length < 8) snippets.push({text: clean(parent.innerText).slice(0, 2000), html: html(parent)});
    }
    return {rows, snippets, membership};
}"""

CLOSE_BANK_DETAILS = r"""() => {
    const dialog = [...document.querySelectorAll('[role="dialog"], [aria-modal="true"], [data-widget="modalLayout"]')].find(el => el.getClientRects().length);
    const close = dialog?.querySelector('[aria-label="Закрыть"], [title="Закрыть"]') ||
      (dialog ? [...dialog.querySelectorAll('button, [role="button"]')].find(el =>
        /^(закрыть|понятно|готово)$/i.test((el.innerText || '').trim())) : null);
    if (close) close.click();
    else document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', code: 'Escape', bubbles: true}));
    delete window.__ozonMonitorBankNodes;
}"""
