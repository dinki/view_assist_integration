"""Override HassSearchAndPlayMedia intent handler."""

from dataclasses import dataclass
from difflib import SequenceMatcher
import logging
import re
from typing import Any, cast, override

import voluptuous as vol

from homeassistant.components.media_player import (
    ATTR_MEDIA_FILTER_CLASSES,
    DOMAIN as MEDIA_PLAYER_DOMAIN,
    INTENT_MEDIA_SEARCH_AND_PLAY,
    SERVICE_PLAY_MEDIA,
    SERVICE_SEARCH_MEDIA,
    BrowseMedia,
    MediaClass,
    MediaPlayerEntityFeature,
    SearchMedia,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv, entity_registry as er, intent

_LOGGER = logging.getLogger(__name__)

# Domain/service used to search via a Music Assistant config entry directly,
# when the target media_player belongs to one. Its results already come with
# artist/album/name split out, so are more reliably matched than the generic
# media_player.search_media results, whose title is a single combined string.
_MUSIC_ASSISTANT_DOMAIN = "music_assistant"
_MUSIC_ASSISTANT_SEARCH_SERVICE = "search"
_MUSIC_ASSISTANT_SEARCH_LIMIT = 5

# Maps the result keys returned by music_assistant.search to our media_class
# values.
_MUSIC_ASSISTANT_RESULT_CLASSES: dict[str, str] = {
    "tracks": "track",
    "albums": "album",
    "radio": "radio",
    "playlists": "playlist",
    "podcasts": "podcast",
    "artists": "artist",
}

# Keywords that hint at the media type the user is asking for, mapped to the
# media_class value we'd expect a result of that type to carry. Checked
# longest-first so "radio station" is matched before the bare "radio".
_MEDIA_TYPE_KEYWORDS: dict[str, str] = {
    "radio station": "radio",
    "podcast": "podcast",
    "playlist": "playlist",
    "album": "album",
    "radio": "radio",
    "artist": "artist",
}

_SUPPORTED_MEDIA_CLASSES = [
    MediaClass.ARTIST,
    MediaClass.ALBUM,
    MediaClass.TRACK,
    MediaClass.PLAYLIST,
    MediaClass.PODCAST,
]

# When the request doesn't name (or imply) a specific media type, prefer
# results in this order.
_DEFAULT_CLASS_PRIORITY = ("track", "album", "radio", "playlist", "podcast")

# Minimum whole-term/title similarity ratio to accept a fuzzy match without
# falling back to splitting the query into title/artist parts.
_FUZZY_MATCH_THRESHOLD = 0.6

_TRAILING_QUALIFIER_RE = re.compile(
    r"\s+(radio station|album|podcast|playlist|radio)\s*$", re.IGNORECASE
)

# The word used to join a title and an artist, e.g. "[song] BY [artist]".
# Keyed on the base (region-less) language code; falls back to English.
_ARTIST_SEPARATOR_WORDS: dict[str, str] = {
    "en": "by",
    "fr": "par",
    "de": "von",
    "es": "de",
    "it": "di",
    "nl": "door",
    "pt": "de",
}
_DEFAULT_ARTIST_SEPARATOR_WORD = "by"


def _get_artist_separator_word(language: str | None) -> str:
    """Return the "by"-equivalent word that joins a title and artist for a language."""
    if not language:
        return _DEFAULT_ARTIST_SEPARATOR_WORD
    base_language = language.split("-", 1)[0].lower()
    return _ARTIST_SEPARATOR_WORDS.get(base_language, _DEFAULT_ARTIST_SEPARATOR_WORD)


@dataclass(slots=True)
class _MediaCandidate:
    """A search result normalised to a common shape, whichever search action produced it."""

    media_class: str
    name: str
    artist: str | None
    media_content_id: str
    media_content_type: str
    raw: BrowseMedia | dict[str, Any]

    def as_speech_dict(self) -> dict[str, Any]:
        """Return a dict describing this result for use in speech slots."""
        if isinstance(self.raw, BrowseMedia):
            return self.raw.as_dict()
        title = f"{self.artist} - {self.name}" if self.artist else self.name
        return {**self.raw, "title": title, "media_class": self.media_class}


class VAMediaSearchAndPlayHandler(intent.IntentHandler):
    """Custom intent handler for media search and play."""

    intent_type = INTENT_MEDIA_SEARCH_AND_PLAY
    description = (
        "Searches for media and plays the first result. "
        "You must provide the query in the 'search_query' field, the media_player_entity_id value in a field called 'name' and "
        "identify if the requested media is a track, album, artist, playlist or podcast and provide that in the 'media_class' field. If you do not know the media class, leave it blank and the tool will try to infer it from the search query. "
        "In your response tell me the media class you are playing if it is not a track.  If an error occurred, the tool will return an error message in the 'error' field. If the tool was successful, it will return a 'success' field with a value of True. "
    )

    @property
    def slot_schema(self) -> dict | None:
        """Return a slot schema."""
        return {
            vol.Required("search_query"): cv.string,
            vol.Optional("media_class"): vol.In([cls.value for cls in MediaClass]),
            # Optional name/area/floor slots handled by intent matcher
            vol.Optional("name"): cv.string,
            vol.Optional("area"): cv.string,
            vol.Optional("floor"): cv.string,
            vol.Optional("preferred_area_id"): cv.string,
            vol.Optional("preferred_floor_id"): cv.string,
        }

    @override
    async def async_handle(
        self, intent_obj: intent.Intent, extra_data: dict[str, any] | None = None
    ) -> intent.IntentResponse:
        """Handle the intent."""
        hass = intent_obj.hass
        slots = self.async_validate_slots(intent_obj.slots)
        query_slot = slots.get("query", {}) | slots.get("search_query", {})
        search_query = query_slot.get("value")

        # Entity name to match
        name_slot = slots.get("name", {})
        entity_name: str | None = name_slot.get("value")

        # Get area/floor info
        area_slot = slots.get("area", {})
        area_id = area_slot.get("value")

        floor_slot = slots.get("floor", {})
        floor_id = floor_slot.get("value")

        # Populate media player from extra_data if not provided in the slots, e.g. from a VADeviceInfo context
        if not entity_name and extra_data:
            entity_name = extra_data.get("media_player")

        # Find matching entities
        match_constraints = intent.MatchTargetsConstraints(
            name=entity_name,
            area_name=None if entity_name else area_id,
            floor_name=None if entity_name else floor_id,
            domains={MEDIA_PLAYER_DOMAIN},
            assistant=intent_obj.assistant,
            features=MediaPlayerEntityFeature.SEARCH_MEDIA
            | MediaPlayerEntityFeature.PLAY_MEDIA,
            single_target=True,
        )
        match_result = intent.async_match_targets(
            hass,
            match_constraints,
            intent.MatchTargetsPreferences(
                area_id=slots.get("preferred_area_id", {}).get("value"),
                floor_id=slots.get("preferred_floor_id", {}).get("value"),
            ),
        )

        if not match_result.is_match:
            raise intent.MatchFailedError(
                result=match_result, constraints=match_constraints
            )

        # if the first match result entity has an _attr_available that is false, raise an error to indicate that the intent cannot be fulfilled
        if (
            state := hass.states.get(match_result.states[0].entity_id)
        ) is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            raise intent.NoStatesMatchedError(intent.MatchFailedReason.STATE)

        target_entity = match_result.states[0]
        target_entity_id = target_entity.entity_id

        # Get media class if provided
        media_class_slot = slots.get("media_class", {})
        media_class_value = media_class_slot.get("value")

        # 1. Search Media - prefer Music Assistant's own search action when
        # the target entity is served by a loaded Music Assistant config
        # entry, since its results already come with artist/album/name
        # split out, giving more reliable matching.
        if config_entry_id := self._get_music_assistant_config_entry_id(
            hass, target_entity_id
        ):
            candidates = await self._async_search_music_assistant(
                hass, intent_obj, config_entry_id, search_query, media_class_value
            )
        else:
            candidates = await self._async_search_default(
                hass, intent_obj, target_entity_id, search_query, media_class_value
            )

        if not candidates:
            # No results found
            resp = intent_obj.create_response()
            resp.async_set_speech(
                f"Sorry, I couldn't find any results for '{search_query}'"
            )
            return resp

        # 2. Play Media (best match for the original search request)
        best_candidate = self._select_best_media_match(
            search_query, candidates, media_class_value, intent_obj.language
        )
        _LOGGER.warning("Best media match: %s", best_candidate.as_speech_dict())
        try:
            await hass.services.async_call(
                MEDIA_PLAYER_DOMAIN,
                SERVICE_PLAY_MEDIA,
                {
                    "entity_id": target_entity_id,
                    "media_content_id": best_candidate.media_content_id,
                    "media_content_type": best_candidate.media_content_type,
                },
                blocking=True,
                context=intent_obj.context,
            )
        except HomeAssistantError as err:
            _LOGGER.error("Error calling play_media: %s", err)
            raise intent.IntentHandleError(f"Error playing media: {err}") from err

        # Success
        response = intent_obj.create_response()
        if best_candidate.media_class not in ("track"):
            sentence = (
                f"Playing the {best_candidate.media_class}, {best_candidate.name}"
            )
        else:
            sentence = f"Playing {best_candidate.name}"
        if best_candidate.artist:
            sentence += f" by {best_candidate.artist}"
        response.async_set_speech(sentence)
        return response

    @staticmethod
    def _parse_search_query(
        search_query: str, language: str | None = None
    ) -> tuple[str, str | None, str | None]:
        """Break a search phrase into (title_term, artist_term, inferred_media_class).

        The search_query no longer includes a leading "play" (or "play
        the"). Handles phrasings such as "[song] by [artist]", "the [album]
        album by [artist]", "[podcast] podcast", "music by [artist]" and
        "[radio station]". The word equivalent to "by" is looked up for the
        given language (see `_ARTIST_SEPARATOR_WORDS`), defaulting to "by".
        """
        text = search_query.strip()

        inferred_class: str | None = None
        for keyword, media_class in _MEDIA_TYPE_KEYWORDS.items():
            if re.search(rf"\b{re.escape(keyword)}\b", text, re.IGNORECASE):
                inferred_class = media_class
                break

        separator = re.escape(_get_artist_separator_word(language))
        music_by_re = re.compile(
            rf"^music\s+{separator}\s+(?P<artist>.+)$", re.IGNORECASE
        )
        by_artist_re = re.compile(
            rf"^(?P<title>.+?)\s+{separator}\s+(?P<artist>.+)$", re.IGNORECASE
        )

        # "music by [artist]" -> artist search, term is the artist name
        if (music_by_match := music_by_re.match(text)) is not None:
            artist_term = music_by_match.group("artist").strip().lower()
            return artist_term, artist_term, "artist"

        if (by_match := by_artist_re.match(text)) is not None:
            title_term = by_match.group("title").strip()
            artist_term = by_match.group("artist").strip().lower()
        else:
            title_term = text.strip()
            artist_term = None

        # Strip trailing qualifiers ("album", "podcast" etc) from the title
        # portion only, now that any "by [artist]" clause has been removed
        title_term = _TRAILING_QUALIFIER_RE.sub("", title_term).strip().lower()

        return title_term, artist_term, inferred_class

    @staticmethod
    def _candidate_from_browse_media(item: BrowseMedia) -> _MediaCandidate:
        """Build a candidate from a generic media_player.search_media result.

        Track/album titles from this action come back as a single
        "[artist] - [name]" string, so split it to approximate the
        artist/name split Music Assistant's own search gives us natively.
        """
        media_class = (item.media_class or "").lower()
        title = (item.title or "").strip()
        name, artist = title, None

        if media_class in ("track", "album") and " - " in title:
            artist_part, _, name_part = title.partition(" - ")
            artist, name = artist_part.strip(), name_part.strip()

        return _MediaCandidate(
            media_class=media_class,
            name=name,
            artist=artist,
            media_content_id=item.media_content_id,
            media_content_type=item.media_content_type,
            raw=item,
        )

    @staticmethod
    def _get_music_assistant_config_entry_id(
        hass: HomeAssistant, entity_id: str
    ) -> str | None:
        """Return the config_entry_id if entity_id is served by a loaded Music Assistant entry."""
        if _MUSIC_ASSISTANT_DOMAIN not in hass.config.components:
            return None

        entity_entry = er.async_get(hass).async_get(entity_id)
        if not entity_entry or not entity_entry.config_entry_id:
            return None

        config_entry = hass.config_entries.async_get_entry(entity_entry.config_entry_id)
        if not config_entry or config_entry.domain != _MUSIC_ASSISTANT_DOMAIN:
            return None
        if config_entry.state is not ConfigEntryState.LOADED:
            return None

        return config_entry.entry_id

    async def _async_search_music_assistant(
        self,
        hass: HomeAssistant,
        intent_obj: intent.Intent,
        config_entry_id: str,
        search_query: str,
        media_class: str | None,
    ) -> list[_MediaCandidate]:
        """Search via Music Assistant's own search action.

        Its results are returned already split into artist/album/name
        fields per item, so give more reliable matching than the generic
        media_player.search_media action's single combined title string.
        """

        title_term, artist_term, inferred_class = self._parse_search_query(
            search_query, intent_obj.language
        )

        search_data: dict[str, Any] = {
            "config_entry_id": config_entry_id,
            "name": title_term,
            "limit": _MUSIC_ASSISTANT_SEARCH_LIMIT,
        }

        if artist_term:
            search_data["artist"] = artist_term

        if media_class or inferred_class:
            search_data["media_type"] = (
                [media_class]
                if media_class
                else [inferred_class]
                if inferred_class
                else []
            )

        _LOGGER.warning("Searching Music Assistant with data: %s", search_data)

        try:
            response = await hass.services.async_call(
                _MUSIC_ASSISTANT_DOMAIN,
                _MUSIC_ASSISTANT_SEARCH_SERVICE,
                search_data,
                blocking=True,
                context=intent_obj.context,
                return_response=True,
            )
        except HomeAssistantError as err:
            _LOGGER.error("Error calling music_assistant.search: %s", err)
            raise intent.IntentHandleError(f"Error searching media: {err}") from err

        candidates: list[_MediaCandidate] = []
        for result_key, item_class in _MUSIC_ASSISTANT_RESULT_CLASSES.items():
            for item in (response or {}).get(result_key) or []:
                artists = item.get("artists") or []
                candidates.append(
                    _MediaCandidate(
                        media_class=item_class,
                        name=item.get("name") or "",
                        artist=artists[0].get("name") if artists else None,
                        media_content_id=item.get("uri"),
                        media_content_type=item.get("media_type"),
                        raw=item,
                    )
                )
        return candidates

    async def _async_search_default(
        self,
        hass: HomeAssistant,
        intent_obj: intent.Intent,
        target_entity_id: str,
        search_query: str,
        media_class: str | None,
    ) -> list[_MediaCandidate]:
        """Search via the generic media_player.search_media action."""
        search_data: dict[str, Any] = {"search_query": search_query}
        search_data[ATTR_MEDIA_FILTER_CLASSES] = (
            [media_class]
            if media_class in _SUPPORTED_MEDIA_CLASSES
            else _SUPPORTED_MEDIA_CLASSES
        )

        try:
            search_response = await hass.services.async_call(
                MEDIA_PLAYER_DOMAIN,
                SERVICE_SEARCH_MEDIA,
                search_data,
                target={"entity_id": target_entity_id},
                blocking=True,
                context=intent_obj.context,
                return_response=True,
            )
        except HomeAssistantError as err:
            _LOGGER.error("Error calling search_media: %s", err)
            raise intent.IntentHandleError(f"Error searching media: {err}") from err

        if (
            not search_response
            or not (
                entity_response := cast(
                    SearchMedia, search_response.get(target_entity_id)
                )
            )
            or not (results := entity_response.result)
        ):
            return []

        return [self._candidate_from_browse_media(item) for item in results]

    def _select_best_media_match(
        self,
        search_query: str,
        candidates: list[_MediaCandidate],
        media_class: str | None = None,
        language: str | None = None,
    ) -> _MediaCandidate:
        """Select the candidate that best matches the original search request.

        Matching is tried in stages, from strictest to loosest, stopping at
        the first stage that produces a match:

        1. An exact (case-insensitive) match of a candidate's name against
           the whole search term.
        2. A fuzzy match of a candidate's name against the whole search
           term (accepted if similarity is at or above
           `_FUZZY_MATCH_THRESHOLD`).
        3. The search term split into title/artist parts - on the
           language's "by"-equivalent separator word - matched separately
           against each candidate's name and artist.

        Within whichever stage produces a match, if more than one candidate
        is equally good, ties are broken by the media_class implied by the
        request (explicitly via `media_class`, or inferred from phrasing
        like "album"/"podcast"/"radio station") and then by the default
        class priority order - track, album, radio, playlist, podcast.
        """
        if not candidates:
            raise ValueError("candidates must not be empty")
        if len(candidates) == 1:
            return candidates[0]

        title_term, artist_term, inferred_class = self._parse_search_query(
            search_query, language
        )
        _LOGGER.warning(
            "Parsed search query: title=%s, artist=%s, inferred_class=%s",
            title_term,
            artist_term,
            inferred_class,
        )

        requested_class = (media_class or inferred_class or "").lower()

        def class_priority_rank(candidate: _MediaCandidate) -> int:
            if requested_class and candidate.media_class == requested_class:
                return -1
            return (
                _DEFAULT_CLASS_PRIORITY.index(candidate.media_class)
                if candidate.media_class in _DEFAULT_CLASS_PRIORITY
                else len(_DEFAULT_CLASS_PRIORITY)
            )

        def best_of(matches: list[_MediaCandidate]) -> _MediaCandidate:
            return min(matches, key=class_priority_rank)

        whole_term = search_query.strip().lower()

        # Stage 1: exact match of the whole search term against the name
        exact_matches = [
            c for c in candidates if c.name and c.name.strip().lower() == whole_term
        ]
        if exact_matches:
            _LOGGER.warning("Exact whole-term match(es): %s", exact_matches)
            return best_of(exact_matches)

        # Stage 2: fuzzy match of the whole search term against the name
        def whole_term_score(candidate: _MediaCandidate) -> float:
            if not candidate.name:
                return 0.0
            return SequenceMatcher(
                None, whole_term, candidate.name.strip().lower()
            ).ratio()

        best_fuzzy_score = max(whole_term_score(c) for c in candidates)
        if best_fuzzy_score >= _FUZZY_MATCH_THRESHOLD:
            fuzzy_matches = [
                c for c in candidates if whole_term_score(c) == best_fuzzy_score
            ]
            _LOGGER.warning(
                "Fuzzy whole-term match(es) (score=%s): %s",
                best_fuzzy_score,
                fuzzy_matches,
            )
            return best_of(fuzzy_matches)

        # Stage 3: split the term into title/artist parts and match them
        # separately - e.g. "[song] by [artist]"
        name_term = (
            artist_term if requested_class == "artist" and artist_term else title_term
        )

        def split_score(candidate: _MediaCandidate) -> float:
            if not candidate.name or not name_term:
                return 0.0
            score = SequenceMatcher(None, name_term, candidate.name.lower()).ratio()
            if candidate.artist and artist_term:
                artist_score = SequenceMatcher(
                    None, artist_term, candidate.artist.lower()
                ).ratio()
                # Name match matters most; artist match confirms/breaks ties
                score = (score * 0.7) + (artist_score * 0.3)
            return score

        best_split_score = max(split_score(c) for c in candidates)
        split_matches = [c for c in candidates if split_score(c) == best_split_score]
        _LOGGER.warning(
            "Split title/artist match(es) (score=%s): %s",
            best_split_score,
            split_matches,
        )
        return best_of(split_matches)
