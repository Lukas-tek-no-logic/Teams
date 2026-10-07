//! JavaScript evaluated inside the Teams page.
//!
//! All scripts are expressions returning JSON-serialisable values. Chat ids come from
//! `data-fui-tree-item-value` on sidebar tree items, e.g.
//! `OneGQL_GroupChatConversation|19:xxx@thread.v2` (possibly nested under folders with `/`).

/// Selectors that indicate the Teams app shell has rendered (= logged in).
pub const APP_SHELL: &str = r#"(() => !!document.querySelector(
  '[data-tid="app-layout"], [data-tid="teams-app-bar"], [data-tid="left-rail"], [data-tid="chat-list"], [role="tree"] [role="treeitem"]'
))()"#;

/// Shared helper: chat id of a tree item, or "" when it is not a real chat.
const CHAT_ID_FN: &str = r#"
const __ttChatId = (el) => {
  const v = el.dataset ? (el.dataset.fuiTreeItemValue || '') : '';
  if (!v) return '';
  const seg = v.split('/').pop();
  if (!seg.includes('Conversation|')) return '';
  if (seg.includes('Folder|') || seg.includes('Slice') || seg.includes('MetaOS') || seg.includes('activities')) return '';
  const id = seg.substring(seg.indexOf('|') + 1);
  if (!id || id.startsWith('19:preview-')) return '';
  return id;
};
const __ttChatPrefix = (el) => {
  const seg = (el.dataset.fuiTreeItemValue || '').split('/').pop();
  return seg.substring(0, seg.indexOf('|'));
};
"#;

pub fn scrape_chats() -> String {
    format!(
        r#"(() => {{
{CHAT_ID_FN}
  const out = [];
  const seen = new Set();
  for (const el of document.querySelectorAll('[role="treeitem"]')) {{
    const id = __ttChatId(el);
    if (!id || seen.has(id)) continue;
    seen.add(id);
    let text = (el.textContent || '').trim();
    if (!text || text.length > 600) continue;
    const aria = (el.getAttribute('aria-label') || '').toLowerCase();
    let unread = /^(nieprzeczytane|unread)/i.test(text) || aria.includes('nieprzeczyt') || aria.includes('unread')
      || !!el.querySelector('[data-tid*="unread"]');
    text = text.replace(/^(Nieprzeczytane|Unread)\s*/i, '').replace(/(Wersja robocza|Draft)\s*$/i, '');
    const m = text.match(/^(.+?)(\d{{1,2}}:\d{{2}}|\d{{1,2}}\.\d{{2}}(?:\.\d{{2,4}})?|wczoraj|yesterday|pon\.|wt\.|śr\.|czw\.|pt\.|sob\.|niedz\.)(.*)$/s);
    let title, time = '', preview = '';
    if (m) {{
      title = m[1].trim(); time = m[2].trim(); preview = m[3].trim().replace(/^Ty:\s*/, 'ja: ');
    }} else {{
      title = text;
    }}
    if (!title) continue;
    out.push({{ id, prefix: __ttChatPrefix(el), title, time, preview: preview.substring(0, 200), unread,
               selected: el.getAttribute('aria-selected') === 'true' }});
  }}
  return out;
}})()"#
    )
}

/// Id of the chat currently open in the browser ("" if none).
pub fn active_chat() -> String {
    format!(
        r#"(() => {{
{CHAT_ID_FN}
  const el = document.querySelector('[role="treeitem"][aria-selected="true"]');
  return el ? __ttChatId(el) : '';
}})()"#
    )
}

/// Scroll the chat's sidebar item into view and return its centre for a trusted click,
/// or null when the chat is not in the sidebar.
pub fn chat_item_point(chat_id: &str) -> String {
    let id = serde_json::to_string(chat_id).unwrap();
    format!(
        r#"(() => {{
{CHAT_ID_FN}
  const want = {id};
  for (const el of document.querySelectorAll('[role="treeitem"]')) {{
    if (__ttChatId(el) !== want) continue;
    el.scrollIntoView({{ block: 'center' }});
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) return null;
    return {{ x: r.left + Math.min(r.width / 2, 120), y: r.top + r.height / 2 }};
  }}
  return null;
}})()"#
    )
}

/// Centre of an element matching one of the selectors (first visible match), or null.
pub fn point_of(selectors: &[&str]) -> String {
    let sels = serde_json::to_string(selectors).unwrap();
    format!(
        r#"(() => {{
  for (const s of {sels}) {{
    const el = document.querySelector(s);
    if (!el) continue;
    el.scrollIntoView({{ block: 'center' }});
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    return {{ x: r.left + r.width / 2, y: r.top + r.height / 2 }};
  }}
  return null;
}})()"#
    )
}

/// Compose box of the open chat. Deliberately no generic `[role=textbox]` fallback:
/// it can match a channel post box and send text to the wrong place.
pub const COMPOSE_SELECTORS: &[&str] = &[
    r#"[data-tid="chat-pane-compose-message-footer"] [contenteditable="true"]"#,
    r#"[data-tid="ckeditor"] [contenteditable="true"]"#,
];

/// Text currently in the compose box (to verify typing before pressing Enter).
pub fn compose_text() -> String {
    let sels = serde_json::to_string(COMPOSE_SELECTORS).unwrap();
    format!(
        r#"(() => {{
  for (const s of {sels}) {{
    const el = document.querySelector(s);
    if (el) return (el.innerText || '').trim();
  }}
  return null;
}})()"#
    )
}

/// Sidebar "Chat" app button (data-tid verified from a live sidebar dump; "Zespoły" is a different id).
pub const CHAT_APP_BUTTON: &str = r#"button[data-tid="86fcd49b-61a2-4701-b771-54728cd291fb"]"#;

/// Messages rendered in the open chat, plus the id of the open chat for verification.
pub fn scrape_messages() -> String {
    format!(
        r#"(() => {{
{CHAT_ID_FN}
  const sel = document.querySelector('[role="treeitem"][aria-selected="true"]');
  const chat = sel ? __ttChatId(sel) : '';
  const out = [];
  let lastSender = '';
  for (const el of document.querySelectorAll('.fui-ChatMessage, .fui-ChatMyMessage')) {{
    const mine = el.classList.contains('fui-ChatMyMessage');
    const author = (el.querySelector('[data-tid="message-author-name"]')?.textContent || '').trim();
    let ts = '';
    for (const e of el.querySelectorAll('[id*="author-"], [id*="timestamp-"]')) {{
      const m = e.id.match(/(?:author|timestamp)-(\d{{10,}})/);
      if (m) {{ ts = m[1]; break; }}
    }}
    if (!ts) {{
      const t = el.querySelector('time[datetime]');
      if (t) {{ const d = Date.parse(t.getAttribute('datetime')); if (!isNaN(d)) ts = String(d); }}
    }}
    const body = el.querySelector('[data-tid="message-body"], .fui-ChatMessage__body, .fui-ChatMyMessage__body');
    const content = (body ? body.innerText : '').trim();
    if (!content || !ts) continue;
    // Consecutive messages from the same person omit the author line.
    const sender = mine ? '' : (author || lastSender);
    if (!mine && author) lastSender = author;
    out.push({{ id: ts, ts, mine, sender, content: content.substring(0, 8000) }});
  }}
  return {{ chat, messages: out }};
}})()"#
    )
}

/// Installed on every document: reports DOM changes through the `__ttEvent` binding,
/// debounced so a burst of mutations costs one round trip.
pub const OBSERVER: &str = r#"
(() => {
  if (window.__ttObserver) return 'already';
  window.__ttObserver = true;
  let chats = false, msgs = false, timer = null, lastTyping = null;
  const send = (o) => { try { window.__ttEvent(JSON.stringify(o)); } catch (e) {} };
  const flush = () => {
    timer = null;
    if (chats || msgs) send({ type: 'dirty', chats, msgs });
    chats = msgs = false;
    const ti = document.querySelector('[data-tid*="typing-indicator"], [data-tid*="typing"]');
    const typing = ti ? (ti.textContent || '').trim() : '';
    if (typing !== lastTyping) { lastTyping = typing; send({ type: 'typing', text: typing }); }
  };
  const obs = new MutationObserver((ms) => {
    for (const m of ms) {
      const t = m.target.nodeType === 1 ? m.target : m.target.parentElement;
      if (!t) continue;
      if (t.closest('[role="tree"]')) chats = true;
      else if (t.closest('.fui-Chat, [data-tid="message-pane-list-viewport"], [data-tid="chat-pane-list"]')
               || t.querySelector?.('.fui-ChatMessage, .fui-ChatMyMessage')) msgs = true;
    }
    if (!timer) timer = setTimeout(flush, 250);
  });
  const start = () => obs.observe(document.body, { childList: true, subtree: true, characterData: true,
                                                    attributes: true, attributeFilter: ['aria-selected', 'aria-label'] });
  if (document.body) start(); else document.addEventListener('DOMContentLoaded', start);
  return 'installed';
})()
"#;
