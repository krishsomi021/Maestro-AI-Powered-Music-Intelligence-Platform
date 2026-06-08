import { apiGet } from './client';
import type { ListeningTrendItem, TopArtistItem, TopTrackItem } from './types';

export function getTopTracks(limit = 10): Promise<{ tracks: TopTrackItem[] }> {
  return apiGet(`/stats/top-tracks?limit=${limit}`);
}

export function getTopArtists(limit = 10): Promise<{ artists: TopArtistItem[] }> {
  return apiGet(`/stats/top-artists?limit=${limit}`);
}

export function getListeningTrends(): Promise<{ trends: ListeningTrendItem[] }> {
  return apiGet('/stats/listening-trends');
}
