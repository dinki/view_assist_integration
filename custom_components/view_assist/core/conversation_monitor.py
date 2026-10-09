"""Chat monitor for handling conversation intents."""

from dataclasses import dataclass
from datetime import datetime
import logging
from typing import Any

from homeassistant.components.assist_satellite.entity import AssistSatelliteState
from homeassistant.components.conversation.chat_log import (
    DATA_CHAT_LOGS,
    AssistantContent,
    ChatLog,
    async_subscribe_chat_logs,
)
from homeassistant.components.conversation.const import ChatLogEventType
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util

from ..const import DOMAIN  # noqa: TID252
from ..helpers import get_key, get_sensor_entity_from_instance  # noqa: TID252
from ..typed import VAConfigEntry, VAEvent, VAEventType  # noqa: TID252

_LOGGER = logging.getLogger(__name__)


@dataclass
class Conversation:
    """Holds conversation information."""

    conversation_id: str
    last_user_content: str | None = None
    last_tool_result: str | None = None
    last_assistant_content: str | None = None
    created: datetime | None = None
    last_updated: datetime | None = None


@dataclass
class EntryConversationId:
    """Holds entry conversation ID information."""

    entry_id: str
    entity_id: str
    status: str
    conversation_id: str
    last_updated: float


class ConversationMonitor:
    """Class to monitor chat interactions."""

    @classmethod
    def get(cls, hass: HomeAssistant) -> ConversationMonitor | None:
        """Get the intents manager for a config entry."""
        try:
            return hass.data[DOMAIN][cls.__name__]
        except KeyError:
            return None

    def __init__(self, hass: HomeAssistant, config: VAConfigEntry) -> None:
        """Initialise."""
        self.hass = hass
        self.config = config

        self.entry_statuses: dict[str, EntryConversationId] = {}
        self.conversations: dict[str, Conversation] = {}

        self._unsubscribe_chat = None

    async def async_setup(self) -> bool:
        """Set up the Intents Manager."""
        self._unsubscribe_chat = async_subscribe_chat_logs(
            self.hass, self._handle_chat_log_event
        )
        return True

    async def async_unload(self) -> bool:
        """Unload the Intents Manager."""
        if self._unsubscribe_chat:
            self._unsubscribe_chat()

        return True

    @callback
    def _handle_chat_log_event(
        self, conversation_id: str, event_type: ChatLogEventType, data: dict[str, Any]
    ) -> None:
        """Handle a chat event."""
        payload = {}
        entry_info = self._get_entry_for_conversation_id(conversation_id)

        if not entry_info:
            entry_info = self._set_conversation_id(conversation_id)

        if event_type == ChatLogEventType.CONTENT_ADDED:
            conversation = self.conversations[conversation_id]
            if content := data.get("content"):
                created = content.get("created")
                if created and created >= entry_info.last_updated:
                    if content.get("role") == "user" and content.get("content"):
                        self.conversations[
                            conversation_id
                        ].last_user_content = content.get("content")
                        conversation.last_updated = dt_util.now()
                        payload = {"command": conversation.last_user_content}
                    elif content.get("role") == "tool_result" and content.get(
                        "tool_name"
                    ):
                        tool_name = content.get("tool_name")
                        tool_name = tool_name.split("__")[-1]
                        self.conversations[conversation_id].last_tool_result = tool_name
                        conversation.last_updated = dt_util.now()
                        payload = {"intent": conversation.last_tool_result}
                    elif content.get("role") == "assistant" and content.get("content"):
                        self.conversations[
                            conversation_id
                        ].last_assistant_content = content.get("content")
                        conversation.last_updated = dt_util.now()
                        payload = {
                            "intent": conversation.last_tool_result,
                            "response": conversation.last_assistant_content,
                        }

        elif event_type == ChatLogEventType.UPDATED:
            # For non intent responses, the response text is only shown in a chat log update event
            # not in a subscribed assistant message.
            # The update is a combination of multiple conversations, so we need to extract the relevant content for this conversation.
            conversation = self.conversations[conversation_id]
            for entry in get_key("chat_log.content", data):
                created = entry.get("created")
                if created and created >= entry_info.last_updated:
                    if entry.get("role") == "assistant" and entry.get("content"):
                        self.conversations[
                            conversation_id
                        ].last_assistant_content = entry.get("content")
                        conversation.last_updated = dt_util.now()
                        payload = {
                            "intent": conversation.last_tool_result,
                            "response": conversation.last_assistant_content,
                        }

        elif event_type == ChatLogEventType.DELETED:
            # Handle deleted chat log entries if necessary
            if conversation_id in self.conversations:
                del self.conversations[conversation_id]
                if entry := self._get_entry_for_conversation_id(conversation_id):
                    self.entry_statuses[entry.entry_id].conversation_id = None

        if entry_info and payload:
            self._send_chat_payload(entry_info.entry_id, payload)

    def register_assist_status(self, entry_id: str, status: str) -> None:
        """Register the assist status for a given entry ID."""

        if status == AssistSatelliteState.IDLE:
            # Remove entry
            if entry_id in self.entry_statuses:
                del self.entry_statuses[entry_id]
            self._prune_conversations()
            return

        if status == AssistSatelliteState.PROCESSING:
            # Add or update entry
            entity_id = get_sensor_entity_from_instance(self.hass, entry_id)
            self.entry_statuses[entry_id] = EntryConversationId(
                entry_id=entry_id,
                entity_id=entity_id,
                status=status,
                conversation_id="",
                last_updated=dt_util.now(),
            )

        if status == AssistSatelliteState.RESPONDING:
            # On first HA load if ask goes to LLM, no event with the response content
            # is fired.  This ensures that if the assist satellite starts responsing without
            # having got the content, it will attempt to retrieve the chat log response.
            if entry_info := self.entry_statuses.get(entry_id):
                conversation = self.conversations.get(entry_info.conversation_id)
                if conversation and not conversation.last_assistant_content:
                    conversation_log: ChatLog | None = self.hass.data.get(
                        DATA_CHAT_LOGS, {}
                    ).get(conversation.conversation_id)
                    if conversation_log:
                        log_entries = conversation_log.content
                        for entry in log_entries:
                            if isinstance(entry, AssistantContent) and entry.content:
                                c_id = conversation.conversation_id
                                self.conversations[
                                    c_id
                                ].last_assistant_content = entry.content
                                self.conversations[c_id].last_updated = dt_util.now()
                                conversation = self.conversations[c_id]
                                payload = {
                                    "intent": conversation.last_tool_result,
                                    "response": conversation.last_assistant_content,
                                }
                                self._send_chat_payload(entry_id, payload)

    def _get_most_recent_processing_entry(self) -> EntryConversationId | None:
        """Get the most recent processing entry."""
        most_recent = None
        for entry in self.entry_statuses.values():
            if entry.status == AssistSatelliteState.PROCESSING:
                if most_recent is None or entry.last_updated > most_recent.last_updated:
                    most_recent = entry

        return most_recent

    def _set_conversation_id(self, conversation_id: str) -> EntryConversationId | None:
        """Set the conversation ID for the most recent processing entry."""
        most_recent = self._get_most_recent_processing_entry()

        if most_recent:
            _LOGGER.debug(
                "Setting conversation ID '%s' for entity_id: %s",
                conversation_id,
                most_recent.entity_id,
            )
            self.entry_statuses[most_recent.entry_id].conversation_id = conversation_id

            # Add conversation entry
            self.conversations[conversation_id] = Conversation(
                conversation_id=conversation_id,
                created=dt_util.now(),
                last_updated=dt_util.now(),
            )
            return self.entry_statuses[most_recent.entry_id]
        return None

    def _prune_conversations(self, expire_after: int = 5) -> None:
        """Prune conversations that have expired."""
        now = dt_util.now()
        to_delete = []
        for conversation_id, conversation in self.conversations.items():
            if (now - conversation.last_updated).total_seconds() > expire_after * 60:
                to_delete.append(conversation_id)

        for conversation_id in to_delete:
            del self.conversations[conversation_id]
            if entry := self._get_entry_for_conversation_id(conversation_id):
                self.entry_statuses[entry.entry_id].conversation_id = None

    def _get_entry_for_conversation_id(
        self, conversation_id: str
    ) -> EntryConversationId | None:
        """Get the entry ID associated with a given conversation ID."""
        for entry in self.entry_statuses.values():
            if entry.conversation_id == conversation_id:
                return entry
        return None

    def _send_chat_payload(self, entry_id: str, payload: dict[str, Any]) -> None:
        """Send the chat payload to the appropriate destination."""
        # Implement the logic to send the chat payload here
        _LOGGER.debug("Sending INTENT UPDATE with payload: %s", payload)
        async_dispatcher_send(
            self.hass,
            f"{DOMAIN}_{entry_id}_event",
            VAEvent(VAEventType.INTENT_UPDATE, payload),
        )
