"""Playlists (FR-3, stage 3D): minimal playlists with ordered items.

Services must not know about HTTP (ADR-005); repositories are protocols
so tests inject fakes.
"""

import uuid
from typing import Protocol

from app.models import Playlist, PlaylistItem, User, Video


class DuplicateItem(Exception):
    """Video already present in the playlist."""


class PlaylistRepo(Protocol):
    async def create(self, playlist: Playlist) -> Playlist: ...

    async def get(self, playlist_id: uuid.UUID) -> Playlist | None: ...

    async def update(self, playlist: Playlist) -> Playlist: ...

    async def delete(self, playlist_id: uuid.UUID) -> None: ...

    async def list_channel_playlists(
        self, channel_id, *, viewer_id: uuid.UUID | None, limit: int, offset: int
    ) -> list[Playlist]: ...

    async def add_item(
        self, playlist_id, video_id, position: int
    ) -> PlaylistItem: ...

    async def remove_item(self, playlist_id, video_id) -> None: ...

    async def list_items_with_videos(
        self, playlist_id
    ) -> list[tuple[PlaylistItem, Video]]: ...

    async def max_position(self, playlist_id) -> int: ...

    async def item_count(self, playlist_id) -> int: ...


def new_playlist(user_id: uuid.UUID, title: str, is_private: bool = False) -> Playlist:
    # id assigned here (not left to the flush-time default) so the row can be
    # returned/serialized before the commit — same pattern as upload_video.
    return Playlist(id=uuid.uuid4(), user_id=user_id, title=title, is_private=is_private)


def can_view_playlist(playlist: Playlist, user: User | None) -> bool:
    """Private playlists are visible to the owner only (API maps that to 404)."""
    if playlist.is_private:
        return user is not None and playlist.user_id == user.id
    return True
