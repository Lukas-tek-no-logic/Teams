//! Width-aware text helpers (Polish diacritics and emoji count by display width, not bytes).

use unicode_width::{UnicodeWidthChar, UnicodeWidthStr};

/// Cut `s` to at most `width` columns, adding `…` when shortened.
pub fn truncate(s: &str, width: usize) -> String {
    if s.width() <= width {
        return s.to_string();
    }
    if width == 0 {
        return String::new();
    }
    let mut out = String::new();
    let mut w = 0;
    for c in s.chars() {
        let cw = c.width().unwrap_or(0);
        if w + cw > width - 1 {
            break;
        }
        out.push(c);
        w += cw;
    }
    out.push('…');
    out
}

/// Pad (or truncate) to exactly `width` columns.
pub fn fit(s: &str, width: usize) -> String {
    let t = truncate(s, width);
    let pad = width.saturating_sub(t.width());
    format!("{t}{}", " ".repeat(pad))
}

/// Greedy word wrap to `width` columns; long words are split.
pub fn wrap(text: &str, width: usize) -> Vec<String> {
    let width = width.max(1);
    let mut lines = Vec::new();
    for para in text.split('\n') {
        let mut line = String::new();
        let mut w = 0;
        for word in para.split(' ') {
            let ww = word.width();
            if w > 0 && w + 1 + ww > width {
                lines.push(std::mem::take(&mut line));
                w = 0;
            }
            if ww > width {
                // Split an over-long word (URLs) at column boundaries.
                for c in word.chars() {
                    let cw = c.width().unwrap_or(0);
                    if w + cw > width {
                        lines.push(std::mem::take(&mut line));
                        w = 0;
                    }
                    line.push(c);
                    w += cw;
                }
                continue;
            }
            if w > 0 {
                line.push(' ');
                w += 1;
            }
            line.push_str(word);
            w += ww;
        }
        lines.push(line);
    }
    lines
}

/// Single-line version of a possibly multi-line text (for previews).
pub fn one_line(s: &str) -> String {
    s.split_whitespace().collect::<Vec<_>>().join(" ")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn wrap_counts_columns_not_bytes() {
        let l = wrap("zażółć gęślą jaźń", 12);
        assert_eq!(l, vec!["zażółć gęślą", "jaźń"]);
    }

    #[test]
    fn truncate_adds_ellipsis() {
        assert_eq!(truncate("Dudziński Łukasz", 6), "Dudzi…");
        assert_eq!(fit("ab", 4), "ab  ");
    }

    #[test]
    fn wrap_splits_long_words() {
        assert_eq!(wrap("abcdefgh", 3), vec!["abc", "def", "gh"]);
    }
}
