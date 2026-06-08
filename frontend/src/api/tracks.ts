import { apiGet } from './client';
import type { TrackSearchResult } from './types';

export function searchTracks(query: string, limit = 20): Promise<{ tracks: TrackSearchResult[] }> {
  return apiGet(`/tracks/search?q=${encodeURIComponent(query)}&limit=${limit}`);
}
