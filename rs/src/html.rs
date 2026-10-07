//! Teams message HTML → plain text for the terminal.

use std::sync::LazyLock;

use regex::Regex;

static BLOCK_END: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?i)<br\s*/?>|</p>|</div>|</li>|</blockquote>|</h[1-6]>").unwrap());
static LI_START: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"(?i)<li[^>]*>").unwrap());
static IMG: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"(?i)<img[^>]*>").unwrap());
static TAG: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"(?s)<[^>]*>").unwrap());
static BLANK_LINES: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\n{3,}").unwrap());

/// Convert Teams HTML content to readable text. Plain text passes through unchanged
/// (apart from entity decoding).
pub fn to_text(html: &str) -> String {
    let mut s = html.replace("\r\n", "\n");
    if s.contains('<') {
        s = BLOCK_END.replace_all(&s, "\n").into_owned();
        s = LI_START.replace_all(&s, "• ").into_owned();
        s = IMG.replace_all(&s, "[obraz]").into_owned();
        s = TAG.replace_all(&s, "").into_owned();
    }
    let s = html_escape::decode_html_entities(&s).replace('\u{a0}', " ");
    let s = BLANK_LINES.replace_all(&s, "\n\n");
    s.lines().map(str::trim_end).collect::<Vec<_>>().join("\n").trim().to_string()
}

/// Lowercase and strip diacritics so "dudzinski" matches "Dudziński".
pub fn fold(s: &str) -> String {
    use unicode_normalization::UnicodeNormalization;
    s.to_lowercase()
        .nfkd()
        .filter(|c| !unicode_normalization::char::is_combining_mark(*c))
        .map(|c| if c == 'ł' { 'l' } else { c })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn paragraphs_and_entities() {
        let t = to_text("<p>Cześć&nbsp;Ania</p><p>a &amp; b</p>");
        assert_eq!(t, "Cześć Ania\na & b");
    }

    #[test]
    fn plain_text_kept() {
        assert_eq!(to_text("tablica[0] < 5"), "tablica[0] < 5");
    }

    #[test]
    fn lists_and_images() {
        assert_eq!(to_text("<ul><li>a</li><li>b</li></ul><img src=x>"), "• a\n• b\n[obraz]");
    }

    #[test]
    fn fold_polish() {
        assert_eq!(fold("Łukasz Dudziński"), "lukasz dudzinski");
    }
}
