import { describe, expect, it } from 'vitest';

import { isValidPassword } from './passwordPolicy';

describe('password policy', () => {
  it('accepts eight characters with English letters, numbers, and a special character', () => {
    expect(isValidPassword('Abcd12!x')).toBe(true);
  });

  it.each(['Abcd1234', '1234567!', 'Abcdefg!', 'Ab1!xyz'])('rejects %s', (password) => {
    expect(isValidPassword(password)).toBe(false);
  });
});
