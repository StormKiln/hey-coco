"""Spotify failures, each with the sentence Coco says about it (``say``)."""

NOT_CONFIGURED_SAY = "Spotify isn't set up yet."
NOT_CONNECTED_SAY = "Spotify needs to be connected on the Mac first."
NO_DEVICE_SAY = "Open Spotify on the iPad once, then try again."
PREMIUM_SAY = "Spotify needs a Premium account for that."
RATE_LIMITED_SAY = "Spotify is busy right now. Try again in a minute."
VOLUME_UNSUPPORTED_SAY = "Spotify won't let me change the iPad's volume. Use its volume buttons."
FAILED_SAY = "I couldn't reach Spotify."


class SpotifyError(Exception):
    """A Spotify request couldn't be done; ``say`` explains why to the household."""

    say = FAILED_SAY

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.say)


class SpotifyNotConfigured(SpotifyError):
    """No ``spotify.client_id`` in the settings."""

    say = NOT_CONFIGURED_SAY


class SpotifyNotConnected(SpotifyError):
    """Configured, but the owner hasn't logged in (or the refresh token was revoked)."""

    say = NOT_CONNECTED_SAY


class SpotifyNoDevice(SpotifyError):
    """The iPad's Spotify app isn't in the Connect device list (or nothing is active)."""

    say = NO_DEVICE_SAY


class SpotifyPremiumRequired(SpotifyError):
    say = PREMIUM_SAY


class SpotifyVolumeUnsupported(SpotifyError):
    """The target device reports ``supports_volume: false``."""

    say = VOLUME_UNSUPPORTED_SAY


class SpotifyRateLimited(SpotifyError):
    say = RATE_LIMITED_SAY

    def __init__(self, retry_after: float) -> None:
        super().__init__(f"Spotify rate limit; retry after {retry_after:g}s")
        self.retry_after = retry_after
