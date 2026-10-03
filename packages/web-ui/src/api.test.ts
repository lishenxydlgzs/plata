import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, newId } from './api';

afterEach(() => vi.unstubAllGlobals());
describe('API failures remain actionable', () => {
  it('reports a lost connection without exposing transport internals', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('network failure')));
    await expect(api('/api/journals')).rejects.toThrow('Cannot reach Plata');
  });
  it('preserves safe server validation messages', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: 'Retry the saved message before sending another.' }), { status: 422 })));
    await expect(api('/api/agents/logbook', 'POST', {})).rejects.toThrow('Retry the saved message');
  });
  it('handles malformed error responses without losing the status', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('<html>Unavailable</html>', { status: 503 })));
    await expect(api('/api/jobs')).rejects.toThrow('(503)');
  });
  it('rejects unreadable successful responses', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('not JSON', { status: 200 })));
    await expect(api('/api/jobs')).rejects.toThrow('unreadable response');
  });
});
it('creates request IDs on ordinary HTTP LAN origins without randomUUID', () => {
  vi.stubGlobal('crypto', { getRandomValues: (bytes: Uint8Array) => bytes.fill(8) });
  expect(newId()).toBe('08'.repeat(16));
});
