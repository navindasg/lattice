/**
 * Utility functions module.
 * Tests: named exports, pure TypeScript with no imports.
 */

export function helper(input: string): string {
  return `[helper] ${input}`;
}

export function formatDate(date: Date): string {
  return date.toISOString();
}

export const VERSION = '1.0.0';
