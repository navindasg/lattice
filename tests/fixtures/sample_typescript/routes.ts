/**
 * Express-style router module.
 * Tests: external import (express), relative import (utils), dynamic import().
 */
import { Router, Request, Response } from 'express';
import { helper } from './utils';

export function getRouter(): Router {
  const router = Router();

  router.get('/health', (_req: Request, res: Response) => {
    res.json({ status: 'ok', helper: helper('ping') });
  });

  router.get('/lazy', async (_req: Request, res: Response) => {
    const lazyModule = await import('./legacy');
    res.json({ loaded: true, module: lazyModule });
  });

  return router;
}

export const BASE_PATH = '/api';
