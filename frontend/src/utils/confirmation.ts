export const normalizeConfirmation = (value: string): string => value.trim();

export const matchesConfirmation = (value: string, expected?: string): boolean =>
  Boolean(expected) && normalizeConfirmation(value) === expected;
