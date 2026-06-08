import { useEffect, useState } from 'react';
import { searchTracks } from '../api/tracks';
import type { TrackSearchResult } from '../api/types';
import './SeedTrackPicker.css';

interface SeedTrackPickerProps {
  seeds: TrackSearchResult[];
  onChange: (seeds: TrackSearchResult[]) => void;
}

export function SeedTrackPicker({ seeds, onChange }: SeedTrackPickerProps) {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<TrackSearchResult[]>([]);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const trimmed = query.trim();
    if (trimmed.length < 2) {
      return;
    }

    let cancelled = false;
    const timer = setTimeout(async () => {
      setSearching(true);
      setError(null);
      try {
        const response = await searchTracks(trimmed);
        if (!cancelled) setResults(response.tracks);
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Search failed');
      } finally {
        if (!cancelled) setSearching(false);
      }
    }, 300);

    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [query]);

  const visibleResults = query.trim().length >= 2 ? results : [];

  function addSeed(track: TrackSearchResult) {
    if (seeds.some((s) => s.uri === track.uri)) return;
    onChange([...seeds, track]);
    setQuery('');
    setResults([]);
  }

  function removeSeed(uri: string) {
    onChange(seeds.filter((s) => s.uri !== uri));
  }

  return (
    <div className="seed-track-picker">
      <label htmlFor="seed-track-search">Search for seed tracks</label>
      <input
        id="seed-track-search"
        type="text"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="Search by track or artist name…"
        autoComplete="off"
        aria-describedby="seed-track-search-status"
      />
      <p id="seed-track-search-status" className="seed-track-picker__status">
        {searching && 'Searching…'}
        {error && <span className="seed-track-picker__error">{error}</span>}
      </p>

      {visibleResults.length > 0 && (
        <ul className="seed-track-picker__results" aria-label="Search results">
          {visibleResults.map((track) => (
            <li key={track.uri}>
              <button type="button" className="secondary" onClick={() => addSeed(track)}>
                {track.track_name} — {track.artist_name}
              </button>
            </li>
          ))}
        </ul>
      )}

      <div>
        <label>Selected seed tracks ({seeds.length})</label>
        {seeds.length === 0 && <p className="seed-track-picker__empty">No seed tracks selected yet.</p>}
        {seeds.length > 0 && (
          <ul className="seed-track-picker__seeds" aria-label="Selected seed tracks">
            {seeds.map((seed) => (
              <li key={seed.uri}>
                <span>
                  {seed.track_name} — {seed.artist_name}
                </span>
                <button
                  type="button"
                  className="secondary"
                  aria-label={`Remove ${seed.track_name} from seeds`}
                  onClick={() => removeSeed(seed.uri)}
                >
                  Remove
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
