from pydantic import BaseModel


class TrackSearchResult(BaseModel):
    uri: str
    track_name: str
    artist_name: str


class TrackSearchResponse(BaseModel):
    tracks: list[TrackSearchResult]
