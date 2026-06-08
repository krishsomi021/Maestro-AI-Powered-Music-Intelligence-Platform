import type { RecommenderResult } from '../api/types';
import './RecommendationResults.css';

export function RecommendationResults({ result }: { result: RecommenderResult }) {
  if (result.recommendations.length === 0) {
    return (
      <p className="recommendation-results__empty">
        No recommendations were found for the selected seed tracks ({result.seed_count} matched the
        model).
      </p>
    );
  }

  return (
    <div>
      <p className="recommendation-results__summary">
        Based on {result.seed_count} matched seed track{result.seed_count === 1 ? '' : 's'}, top{' '}
        {result.top_n}:
      </p>
      <ol className="recommendation-results__list">
        {result.recommendations.map((rec) => (
          <li key={rec.uri}>
            <span className="recommendation-results__track">{rec.track_name}</span>
            <span className="recommendation-results__artist">{rec.artist_name}</span>
            <span className="recommendation-results__score">{rec.score.toFixed(3)}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}
