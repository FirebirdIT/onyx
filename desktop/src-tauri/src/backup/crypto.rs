//! Backup keys and encryption, as the shared contract specifies:
//!
//! - `master = Argon2id(passphrase, salt = sha256("deepx-backup-v1:" + lowercase(email)),
//!   m = 64 MiB, t = 3, p = 1, 32 bytes)`
//! - `backupId`, `token` and `encKey` come from HKDF-SHA256 of `master`.
//! - The payload is encrypted with XChaCha20-Poly1305 in 1 MiB chunks. Each
//!   chunk has a random nonce. The AAD is the header, the chunk index and a
//!   "last chunk" flag, so the order of the chunks cannot change and the file
//!   cannot be cut short without detection.
//!
//! File format: `"DXB1" | version (1 byte)`, then for each chunk
//! `nonce (24 bytes) | ciphertext length (u32 LE) | ciphertext`.

use argon2::{Algorithm, Argon2, Params, Version};
use chacha20poly1305::aead::{Aead, KeyInit, Payload};
use chacha20poly1305::{XChaCha20Poly1305, XNonce};
use hkdf::Hkdf;
use sha2::{Digest, Sha256};
use std::io::{BufRead, BufReader, Read, Write};

pub const MAGIC: &[u8; 4] = b"DXB1";
pub const FORMAT_VERSION: u8 = 1;
pub const CHUNK_SIZE: usize = 1024 * 1024;
const TAG_SIZE: usize = 16;
const NONCE_SIZE: usize = 24;
const HEADER: [u8; 5] = [MAGIC[0], MAGIC[1], MAGIC[2], MAGIC[3], FORMAT_VERSION];

/// Keys for one email + passphrase pair. `Debug` does not show them.
pub struct BackupKeys {
    pub backup_id: String,
    pub token: String,
    enc_key: [u8; 32],
}

impl std::fmt::Debug for BackupKeys {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("BackupKeys { .. }")
    }
}

pub fn normalize_email(email: &str) -> String {
    email.trim().to_lowercase()
}

fn hkdf_32(master: &[u8; 32], info: &str) -> Result<[u8; 32], String> {
    let mut out = [0u8; 32];
    Hkdf::<Sha256>::new(None, master)
        .expand(info.as_bytes(), &mut out)
        .map_err(|_| "Key derivation failed".to_string())?;
    Ok(out)
}

/// Make the backup keys. Slow on purpose (Argon2id with 64 MiB): run it on
/// a blocking thread.
pub fn derive_keys(email: &str, passphrase: &str) -> Result<BackupKeys, String> {
    let salt = Sha256::digest(format!("deepx-backup-v1:{}", normalize_email(email)).as_bytes());
    let params = Params::new(64 * 1024, 3, 1, Some(32))
        .map_err(|e| format!("Argon2 parameters are not valid: {e}"))?;
    let mut master = [0u8; 32];
    Argon2::new(Algorithm::Argon2id, Version::V0x13, params)
        .hash_password_into(passphrase.as_bytes(), &salt, &mut master)
        .map_err(|e| format!("Key derivation failed: {e}"))?;
    Ok(BackupKeys {
        backup_id: hex::encode(hkdf_32(&master, "backup-id")?),
        token: hex::encode(hkdf_32(&master, "backup-token")?),
        enc_key: hkdf_32(&master, "backup-enc")?,
    })
}

fn chunk_aad(index: u64, last: bool) -> [u8; 14] {
    let mut aad = [0u8; 14];
    aad[..5].copy_from_slice(&HEADER);
    aad[5..13].copy_from_slice(&index.to_le_bytes());
    aad[13] = u8::from(last);
    aad
}

/// Read until `buf` is full or the input ends. Returns the byte count.
fn read_full(reader: &mut impl Read, buf: &mut [u8]) -> Result<usize, String> {
    let mut filled = 0;
    while filled < buf.len() {
        match reader.read(&mut buf[filled..]) {
            Ok(0) => break,
            Ok(n) => filled += n,
            Err(e) if e.kind() == std::io::ErrorKind::Interrupted => {}
            Err(e) => return Err(format!("Read failed: {e}")),
        }
    }
    Ok(filled)
}

pub fn encrypt_stream(
    keys: &BackupKeys,
    input: impl Read,
    mut output: impl Write,
) -> Result<(), String> {
    let cipher = XChaCha20Poly1305::new(chacha20poly1305::Key::from_slice(&keys.enc_key));
    let mut reader = BufReader::with_capacity(CHUNK_SIZE, input);
    let write_err = |e: std::io::Error| format!("Write failed: {e}");
    output.write_all(&HEADER).map_err(write_err)?;

    let mut current = vec![0u8; CHUNK_SIZE];
    let mut len = read_full(&mut reader, &mut current)?;
    let mut index: u64 = 0;
    loop {
        let last = reader
            .fill_buf()
            .map_err(|e| format!("Read failed: {e}"))?
            .is_empty();
        let nonce_bytes = crate::secure_random::random_bytes::<NONCE_SIZE>();
        let aad = chunk_aad(index, last);
        let ciphertext = cipher
            .encrypt(
                XNonce::from_slice(&nonce_bytes),
                Payload {
                    msg: &current[..len],
                    aad: &aad,
                },
            )
            .map_err(|_| "Encryption failed".to_string())?;
        let ct_len =
            u32::try_from(ciphertext.len()).map_err(|_| "Chunk is too large".to_string())?;
        output.write_all(&nonce_bytes).map_err(write_err)?;
        output.write_all(&ct_len.to_le_bytes()).map_err(write_err)?;
        output.write_all(&ciphertext).map_err(write_err)?;
        if last {
            break;
        }
        index += 1;
        len = read_full(&mut reader, &mut current)?;
    }
    output.flush().map_err(write_err)
}

const DECRYPT_FAILED: &str =
    "The backup could not be decrypted. Check the email and the passphrase.";

pub fn decrypt_stream(
    keys: &BackupKeys,
    input: impl Read,
    mut output: impl Write,
) -> Result<(), String> {
    let cipher = XChaCha20Poly1305::new(chacha20poly1305::Key::from_slice(&keys.enc_key));
    let mut reader = BufReader::with_capacity(CHUNK_SIZE + 64, input);
    let mut header = [0u8; 5];
    if read_full(&mut reader, &mut header)? != header.len() || header[..4] != MAGIC[..] {
        return Err("This file is not a Deep X backup.".to_string());
    }
    if header[4] != FORMAT_VERSION {
        return Err("This backup needs a newer version of Deep X.".to_string());
    }

    let mut index: u64 = 0;
    let mut ciphertext = vec![0u8; CHUNK_SIZE + TAG_SIZE];
    loop {
        let mut nonce = [0u8; NONCE_SIZE];
        let mut len_bytes = [0u8; 4];
        if read_full(&mut reader, &mut nonce)? != NONCE_SIZE
            || read_full(&mut reader, &mut len_bytes)? != 4
        {
            return Err("The backup is incomplete.".to_string());
        }
        let ct_len = u32::from_le_bytes(len_bytes) as usize;
        if !(TAG_SIZE..=CHUNK_SIZE + TAG_SIZE).contains(&ct_len)
            || read_full(&mut reader, &mut ciphertext[..ct_len])? != ct_len
        {
            return Err("The backup is incomplete.".to_string());
        }
        let last = reader
            .fill_buf()
            .map_err(|e| format!("Read failed: {e}"))?
            .is_empty();
        let aad = chunk_aad(index, last);
        let plaintext = cipher
            .decrypt(
                XNonce::from_slice(&nonce),
                Payload {
                    msg: &ciphertext[..ct_len],
                    aad: &aad,
                },
            )
            .map_err(|_| DECRYPT_FAILED.to_string())?;
        output
            .write_all(&plaintext)
            .map_err(|e| format!("Write failed: {e}"))?;
        if last {
            return output.flush().map_err(|e| format!("Write failed: {e}"));
        }
        index += 1;
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)]
    use super::*;
    use std::sync::OnceLock;

    // Argon2 with 64 MiB is slow in debug builds, so derive once.
    fn keys() -> &'static BackupKeys {
        static KEYS: OnceLock<BackupKeys> = OnceLock::new();
        KEYS.get_or_init(|| derive_keys("User@Example.com ", "correct horse").unwrap())
    }

    fn round_trip(data: &[u8]) -> Vec<u8> {
        let mut encrypted = Vec::new();
        encrypt_stream(keys(), data, &mut encrypted).unwrap();
        assert_eq!(&encrypted[..4], MAGIC);
        let mut decrypted = Vec::new();
        decrypt_stream(keys(), encrypted.as_slice(), &mut decrypted).unwrap();
        decrypted
    }

    #[test]
    fn derived_ids_are_hex_and_email_case_does_not_matter() {
        let keys = keys();
        assert_eq!(keys.backup_id.len(), 64);
        assert_eq!(keys.token.len(), 64);
        assert!(keys
            .backup_id
            .chars()
            .all(|c| matches!(c, '0'..='9' | 'a'..='f')));
        assert_ne!(keys.backup_id, keys.token);
        let same = derive_keys("user@example.com", "correct horse").unwrap();
        assert_eq!(same.backup_id, keys.backup_id);
        assert!(!format!("{keys:?}").contains(&keys.token));
    }

    #[test]
    fn round_trip_empty_small_and_multi_chunk() {
        assert_eq!(round_trip(b""), Vec::<u8>::new());
        assert_eq!(round_trip(b"pg_dump bytes"), b"pg_dump bytes");
        let big: Vec<u8> = (0..(CHUNK_SIZE * 2 + 123))
            .map(|i| u8::try_from(i % 251).unwrap())
            .collect();
        assert_eq!(round_trip(&big), big);
        let exact = vec![7u8; CHUNK_SIZE];
        assert_eq!(round_trip(&exact), exact);
    }

    #[test]
    fn tampering_and_truncation_fail() {
        let data: Vec<u8> = (0..(CHUNK_SIZE + 10))
            .map(|i| u8::try_from(i % 13).unwrap())
            .collect();
        let mut encrypted = Vec::new();
        encrypt_stream(keys(), data.as_slice(), &mut encrypted).unwrap();

        let mut flipped = encrypted.clone();
        flipped[40] ^= 1;
        assert!(decrypt_stream(keys(), flipped.as_slice(), Vec::new()).is_err());

        // Cut after the first chunk: that chunk was not marked as the last.
        let first_chunk_end = 5 + NONCE_SIZE + 4 + CHUNK_SIZE + TAG_SIZE;
        let truncated = &encrypted[..first_chunk_end];
        assert!(decrypt_stream(keys(), truncated, Vec::new()).is_err());

        assert!(decrypt_stream(keys(), &b"NOPE1"[..], Vec::new()).is_err());
    }
}
