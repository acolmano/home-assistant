"""Support for interacting with Spotify Connect."""
from asyncio import run_coroutine_threadsafe
import datetime as dt
from datetime import timedelta
import logging
from typing import Any, Callable, Dict, List, Optional

from spotipy import Spotify
import voluptuous as vol

from homeassistant.components.media_player import MediaPlayerDevice
from homeassistant.components.media_player.const import (
    ATTR_MEDIA_CONTENT_ID,
    MEDIA_TYPE_MUSIC,
    MEDIA_TYPE_PLAYLIST,
    SUPPORT_NEXT_TRACK,
    SUPPORT_PAUSE,
    SUPPORT_PLAY,
    SUPPORT_PLAY_MEDIA,
    SUPPORT_PREVIOUS_TRACK,
    SUPPORT_SEEK,
    SUPPORT_SELECT_SOURCE,
    SUPPORT_SHUFFLE_SET,
    SUPPORT_VOLUME_SET,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_ID,
    CONF_NAME,
    STATE_IDLE,
    STATE_PAUSED,
    STATE_PLAYING,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.config_entry_oauth2_flow import (
    OAuth2Session,
    async_get_config_entry_implementation,
)
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.entity import Entity
from homeassistant.util.dt import utc_from_timestamp

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

SERVICE_PLAY_PLAYLIST = "play_playlist"
ATTR_RANDOM_SONG = "random_song"

PLAY_PLAYLIST_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_MEDIA_CONTENT_ID): cv.string,
        vol.Optional(ATTR_RANDOM_SONG, default=False): cv.boolean,
    }
)

ICON = "mdi:spotify"

SCAN_INTERVAL = timedelta(seconds=30)

SUPPORT_SPOTIFY = (
    SUPPORT_NEXT_TRACK
    | SUPPORT_PAUSE
    | SUPPORT_PLAY
    | SUPPORT_PLAY_MEDIA
    | SUPPORT_PREVIOUS_TRACK
    | SUPPORT_SEEK
    | SUPPORT_SELECT_SOURCE
    | SUPPORT_SHUFFLE_SET
    | SUPPORT_VOLUME_SET
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: Callable[[List[Entity], bool], None],
) -> None:
    """Set up Spotify based on a config entry."""
    implementation = await async_get_config_entry_implementation(hass, entry)
    session = OAuth2Session(hass, entry, implementation)
    spotify = SpotifyMediaPlayer(session, entry.data[CONF_ID], entry.data[CONF_NAME])
    async_add_entities([spotify], True)


class SpotifyMediaPlayer(MediaPlayerDevice):
    """Representation of a Spotify controller."""

    def __init__(self, session, user_id, name):
        """Initialize."""
        self._name = f"Spotify {name}"
        self._id = user_id
        self._session = session

        self._currently_playing: Optional[dict] = {}
        self._devices: Optional[List[dict]] = []
        self._me: Optional[dict] = None
        self._playlist: Optional[dict] = None
        self._spotify: Spotify = None

    @property
    def name(self) -> str:
        """Return the name."""
        return self._name

    @property
    def icon(self) -> str:
        """Return the icon."""
        return ICON

    @property
    def unique_id(self) -> str:
        """Return the unique ID."""
        return self._id

    @property
    def device_info(self) -> Dict[str, Any]:
        """Return device information about this entity."""
        if self._me is not None:
            model = self._me["product"]

        return {
            "identifiers": {(DOMAIN, self._id)},
            "manufacturer": "Spotify AB",
            "model": f"Spotify {model}".rstrip(),
            "name": self._name,
        }

    @property
    def state(self) -> Optional[str]:
        """Return the playback state."""
        if not self._currently_playing:
            return STATE_IDLE
        if self._currently_playing["is_playing"]:
            return STATE_PLAYING
        return STATE_PAUSED

    @property
    def volume_level(self) -> Optional[float]:
        """Return the device volume."""
        return self._currently_playing.get("device", {}).get("volume_percent", 0) / 100

    @property
    def media_content_id(self) -> Optional[str]:
        """Return the media URL."""
        return self._currently_playing.get("item", {}).get("name")

    @property
    def media_content_type(self) -> Optional[str]:
        """Return the media type."""
        return MEDIA_TYPE_MUSIC

    @property
    def media_duration(self) -> Optional[int]:
        """Duration of current playing media in seconds."""
        if self._currently_playing.get("item") is None:
            return None
        return self._currently_playing["item"]["duration_ms"] / 1000

    @property
    def media_position(self) -> Optional[str]:
        """Position of current playing media in seconds."""
        if not self._currently_playing:
            return None
        return self._currently_playing["progress_ms"] / 1000

    @property
    def media_position_updated_at(self) -> Optional[dt.datetime]:
        """When was the position of the current playing media valid."""
        if not self._currently_playing:
            return None
        return utc_from_timestamp(self._currently_playing["timestamp"] / 1000)

    @property
    def media_image_url(self) -> Optional[str]:
        """Return the media image URL."""
        if (
            self._currently_playing.get("item") is None
            or not self._currently_playing["item"]["album"]["images"]
        ):
            return None
        return self._currently_playing["item"]["album"]["images"][0]["url"]

    @property
    def media_image_remotely_accessible(self) -> bool:
        """If the image url is remotely accessible."""
        return False

    @property
    def media_title(self) -> Optional[str]:
        """Return the media title."""
        return self._currently_playing.get("item", {}).get("name")

    @property
    def media_artist(self) -> Optional[str]:
        """Return the media artist."""
        if self._currently_playing.get("item") is None:
            return None
        return ", ".join(
            [artist["name"] for artist in self._currently_playing["item"]["artists"]]
        )

    @property
    def media_album_name(self) -> Optional[str]:
        """Return the media album."""
        if self._currently_playing.get("item") is None:
            return None
        return self._currently_playing["item"]["album"]["name"]

    @property
    def media_track(self) -> Optional[int]:
        """Track number of current playing media, music track only."""
        return self._currently_playing.get("item", {}).get("track_number")

    @property
    def media_playlist(self):
        """Title of Playlist currently playing."""
        if self._playlist is None:
            return None
        return self._playlist["name"]

    @property
    def source(self) -> Optional[str]:
        """Return the current playback device."""
        return self._currently_playing.get("device", {}).get("name")

    @property
    def source_list(self) -> Optional[List[str]]:
        """Return a list of source devices."""
        if not self._devices:
            return None
        return [device["name"] for device in self._devices]

    @property
    def shuffle(self) -> bool:
        """Shuffling state."""
        return bool(self._currently_playing.get("shuffle_state"))

    @property
    def supported_features(self) -> int:
        """Return the media player features that are supported."""
        if (
            self._me is not None and self._me["product"] != "premium"
        ) or self._currently_playing.get("device", {}).get("is_restricted", True):
            return 0
        return SUPPORT_SPOTIFY

    def set_volume_level(self, volume: int) -> None:
        """Set the volume level."""
        self._spotify.volume(int(volume * 100))

    def media_play(self) -> None:
        """Start or resume playback."""
        self._spotify.start_playback()

    def media_pause(self) -> None:
        """Pause playback."""
        self._spotify.pause_playback()

    def media_previous_track(self) -> None:
        """Skip to previous track."""
        self._spotify.previous_track()

    def media_next_track(self) -> None:
        """Skip to next track."""
        self._spotify.next_track()

    def media_seek(self, position):
        """Send seek command."""
        self._spotify.seek_track(int(position * 1000))

    def play_media(self, media_type: str, media_id: str, **kwargs) -> None:
        """Play media."""
        kwargs = {}

        if not media_id.startswith("spotify:"):
            _LOGGER.error("Media ID must be Spotify URI ('spotify:')")
            return

        if media_type == MEDIA_TYPE_MUSIC:
            kwargs["uris"] = [media_id]
        elif media_type == MEDIA_TYPE_PLAYLIST:
            kwargs["context_uri"] = media_id
        else:
            _LOGGER.error("Media type %s is not supported", media_type)
            return

        self._spotify.start_playback(**kwargs)

    def select_source(self, source: str) -> None:
        """Select playback device."""
        for device in self._devices:
            if device["name"] == source:
                self._spotify.transfer_playback(
                    device["id"], self.state == STATE_PLAYING
                )
                return

    def set_shuffle(self, shuffle: bool) -> None:
        """Enable/Disable shuffle mode."""
        self._spotify.shuffle(shuffle)

    def update(self) -> None:
        """Update state and attributes."""
        if not self._session.valid_token or self._spotify is None:
            run_coroutine_threadsafe(
                self._session.async_ensure_token_valid(), self.hass.loop
            ).result()

            self._spotify = Spotify(auth=self._session.token["access_token"])
            self._me = self._spotify.me()

        current = self._spotify.current_playback()
        self._currently_playing = current if current is not None else {}

        self._playlist = None
        if current.get("context", {}).get("type") == MEDIA_TYPE_PLAYLIST:
            self._playlist = self._spotify.playlist(current["context"]["uri"])

        devices = self._spotify.devices()
        self._devices = devices["devices"] if devices is not None else []
