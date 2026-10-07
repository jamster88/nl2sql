/**
 * A password for an administrator to hand to someone, who should change it.
 *
 * From the browser's own cryptographic generator, over letters and digits
 * that cannot be mistaken for each other when read aloud or copied by hand.
 */

const ALPHABET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789";

export function generatePassword(length = 20, random: (bytes: Uint8Array) => Uint8Array = (bytes) => crypto.getRandomValues(bytes)): string {
  // Rejection sampling: a byte past the last whole multiple of the alphabet
  // would make the first few characters likelier than the rest.
  const limit = 256 - (256 % ALPHABET.length);
  let out = "";
  while (out.length < length) {
    for (const byte of random(new Uint8Array(length))) {
      if (byte < limit && out.length < length) out += ALPHABET[byte % ALPHABET.length];
    }
  }
  return out;
}
