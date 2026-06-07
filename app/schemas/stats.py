from pydantic import BaseModel


class TopTrackItem(BaseModel):
    track_name: str
    artist_name: str
    play_count: int
    total_ms_played: int


class TopTracksResponse(BaseModel):
    tracks: list[TopTrackItem]


class TopArtistItem(BaseModel):
    artist_name: str
    play_count: int
    total_ms_played: int
    unique_tracks: int


class TopArtistsResponse(BaseModel):
    artists: list[TopArtistItem]


class ListeningTrendItem(BaseModel):
    month: str
    play_count: int
    total_ms_played: int


class ListeningTrendsResponse(BaseModel):
    trends: list[ListeningTrendItem]
