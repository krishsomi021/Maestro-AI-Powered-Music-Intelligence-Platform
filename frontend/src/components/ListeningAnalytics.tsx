import { useEffect, useState } from 'react';
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { getListeningTrends, getTopArtists, getTopTracks } from '../api/stats';
import type { ListeningTrendItem, TopArtistItem, TopTrackItem } from '../api/types';
import { Card } from './Card';
import './ListeningAnalytics.css';

function msToMinutes(ms: number): number {
  return Math.round(ms / 60000);
}

export function ListeningAnalytics() {
  const [topTracks, setTopTracks] = useState<TopTrackItem[] | null>(null);
  const [topArtists, setTopArtists] = useState<TopArtistItem[] | null>(null);
  const [trends, setTrends] = useState<ListeningTrendItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([getTopTracks(10), getTopArtists(10), getListeningTrends()])
      .then(([tracks, artists, trendsResponse]) => {
        if (cancelled) return;
        setTopTracks(tracks.tracks);
        setTopArtists(artists.artists);
        setTrends(trendsResponse.trends);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to load stats');
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (error) {
    return <p>Failed to load listening analytics: {error}</p>;
  }

  return (
    <div className="listening-analytics">
      <Card title="Listening trends" description="Plays per month, from your imported streaming history.">
        {trends && trends.length > 0 ? (
          <ResponsiveContainer width="100%" height={240}>
            <LineChart data={trends}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
              <XAxis dataKey="month" />
              <YAxis allowDecimals={false} />
              <Tooltip />
              <Line type="monotone" dataKey="play_count" stroke="var(--color-accent)" strokeWidth={2} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        ) : (
          <p className="listening-analytics__empty">No listening history imported yet.</p>
        )}
      </Card>

      <div className="listening-analytics__tables">
        <Card title="Top tracks">
          {topTracks && topTracks.length > 0 ? (
            <table>
              <thead>
                <tr>
                  <th>Track</th>
                  <th>Artist</th>
                  <th>Plays</th>
                  <th>Minutes</th>
                </tr>
              </thead>
              <tbody>
                {topTracks.map((t) => (
                  <tr key={`${t.artist_name}-${t.track_name}`}>
                    <td>{t.track_name}</td>
                    <td>{t.artist_name}</td>
                    <td>{t.play_count}</td>
                    <td>{msToMinutes(t.total_ms_played)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="listening-analytics__empty">No listening history imported yet.</p>
          )}
        </Card>

        <Card title="Top artists">
          {topArtists && topArtists.length > 0 ? (
            <table>
              <thead>
                <tr>
                  <th>Artist</th>
                  <th>Plays</th>
                  <th>Unique tracks</th>
                  <th>Minutes</th>
                </tr>
              </thead>
              <tbody>
                {topArtists.map((a) => (
                  <tr key={a.artist_name}>
                    <td>{a.artist_name}</td>
                    <td>{a.play_count}</td>
                    <td>{a.unique_tracks}</td>
                    <td>{msToMinutes(a.total_ms_played)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="listening-analytics__empty">No listening history imported yet.</p>
          )}
        </Card>
      </div>
    </div>
  );
}
