//! Random values from the operating system's secure random source.

use chacha20poly1305::aead::rand_core::RngCore;
use chacha20poly1305::aead::OsRng;

pub fn random_bytes<const N: usize>() -> [u8; N] {
    let mut bytes = [0u8; N];
    OsRng.fill_bytes(&mut bytes);
    bytes
}

/// `N` random bytes as lowercase hex (`2 * N` characters).
pub fn random_hex<const N: usize>() -> String {
    hex::encode(random_bytes::<N>())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn random_hex_has_expected_length_and_varies() {
        let first = random_hex::<32>();
        assert_eq!(first.len(), 64);
        assert!(first.chars().all(|c| c.is_ascii_hexdigit()));
        assert_ne!(first, random_hex::<32>());
    }
}
