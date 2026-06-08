import type { ReactNode } from 'react';
import './Card.css';

interface CardProps {
  title: string;
  description?: string;
  children: ReactNode;
}

export function Card({ title, description, children }: CardProps) {
  return (
    <section className="card">
      <header className="card__header">
        <h2>{title}</h2>
        {description && <p className="card__description">{description}</p>}
      </header>
      <div className="card__body">{children}</div>
    </section>
  );
}
