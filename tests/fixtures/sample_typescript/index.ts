/**
 * Barrel file — re-exports from routes and utils.
 * Tests: barrel exports (export * from), named re-exports, static imports.
 */
import { getRouter } from './routes';

export * from './routes';
export { helper, formatDate } from './utils';

export const apiVersion = '1.0.0';

export function createApp() {
  const router = getRouter();
  return { router, version: apiVersion };
}
