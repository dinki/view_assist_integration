"""View Assist LLM API."""

from functools import cache, partial
import logging
from random import choice

import slugify as unicode_slug

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import intent, llm
from homeassistant.util import dt as dt_util

from ..const import DOMAIN  # noqa: TID252
from ..helpers import (  # noqa: TID252
    get_entity_attribute,
    get_entity_id_from_conversation_device_id,
)
from ..typed import VAConfigEntry  # noqa: TID252
from .intents import DeviceInfoData

_LOGGER = logging.getLogger(__name__)


class LLMManager:
    """Manage VA custom LLM API and tools."""

    @classmethod
    def get(cls, hass: HomeAssistant) -> LLMManager | None:
        """Get the intents manager for a config entry."""
        try:
            return hass.data[DOMAIN][cls.__name__]
        except KeyError:
            return None

    def __init__(self, hass: HomeAssistant, config: VAConfigEntry) -> None:
        """Initialise."""
        self.hass = hass
        self.config = config

    async def async_setup(self) -> bool:
        """Set up the LLM Manager."""

        # Register LLM API
        self.config.async_on_unload(
            llm.async_register_api(self.hass, VAAssistAPI(self.hass))
        )

        return True

    async def async_unload(self) -> bool:
        """Unload the Intents Manager."""
        # Currently nothing to unload
        return True


class VAAssistAPI(llm.API):
    """API exposing Assist API to LLMs."""

    IGNORE_INTENTS = {
        intent.INTENT_GET_TEMPERATURE,
        intent.INTENT_GET_STATE,
        intent.INTENT_NEVERMIND,
        intent.INTENT_TOGGLE,
        intent.INTENT_GET_CURRENT_DATE,
        intent.INTENT_GET_CURRENT_TIME,
        intent.INTENT_RESPOND,
    }

    def __init__(self, hass: HomeAssistant) -> None:
        """Init the class."""
        super().__init__(
            hass=hass,
            id="view_assist",
            name="ViewAssist",
        )
        self.cached_slugify = cache(
            partial(unicode_slug.slugify, separator="_", lowercase=False)
        )

    async def async_get_api_instance(
        self, llm_context: llm.LLMContext
    ) -> llm.APIInstance:
        """Return the instance of the API."""

        return llm.APIInstance(
            api=self,
            api_prompt=self._async_get_api_prompt(llm_context, None),
            llm_context=llm_context,
            tools=self._async_get_tools(llm_context, None),
            custom_serializer=llm.selector_serializer,
        )

    @callback
    def _async_get_api_prompt(
        self, llm_context: llm.LLMContext, exposed_entities: dict | None
    ) -> str:
        return "\n".join(
            [
                *self._async_get_preable(llm_context),
                *self._async_get_exposed_entities_prompt(llm_context, exposed_entities),
            ]
        )

    @callback
    def _async_get_preable(self, llm_context: llm.LLMContext) -> list[str]:
        """Return the prompt for the API."""
        prompt = []

        if llm_context.device_id:
            device_info = DeviceInfoData(self.hass, llm_context.device_id)
            prompt.append(
                f"This device_id is {llm_context.device_id}. "
                f"The friendly name for this device is {device_info.entity_name}. "
                f"The media player entity id for this device is {device_info.media_player} and should be used for any media playback commands. "
            )

        # Base info
        prompt.append(
            "If you need information about this device use the VADeviceInfo tool. "
        )

        if not llm_context.device_id or not llm.async_device_supports_timers(
            self.hass, llm_context.device_id
        ):
            prompt.append("This device is not able to start timers.")

        return prompt

    @callback
    def _async_get_exposed_entities_prompt(
        self, llm_context: llm.LLMContext, exposed_entities: dict | None
    ) -> list[str]:
        """Return the prompt for the API for exposed entities."""
        return []

    @callback
    def _async_get_tools(
        self, llm_context: llm.LLMContext, exposed_entities: dict | None
    ) -> list[llm.Tool]:
        """Return a list of LLM tools."""

        tools: list[llm.Tool] = []

        tools.append(VATestTool())
        tools.append(VADeviceInfoTool())
        # tools.append(VAAlarmTimerReminderIntentHandler())

        return tools


class VADeviceInfoTool(llm.Tool):
    """Tool to get device info."""

    name = "VADeviceInfo"
    description = "Gets device information such as name, media player etc."

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> llm.JsonObjectType:
        """Get the current date and time."""
        device_id = llm_context.device_id

        _LOGGER.warning(
            "VADeviceInfoTool called with tool input: %s\nAnd context: %s",
            tool_input,
            llm_context,
        )

        if device_id:
            entity_id = get_entity_id_from_conversation_device_id(hass, device_id)
            name = (
                get_entity_attribute(hass, entity_id, "friendly_name")
                if entity_id
                else None
            )
            media_player = (
                get_entity_attribute(hass, entity_id, "musicplayer_device")
                if entity_id
                else None
            )

        return {
            "success": True,
            "result": {
                "name": name,
                "media_player": media_player,
            },
        }


class VATestTool(llm.Tool):
    """Test Tool for View Assist - not sure what it does yet."""

    name = "VAFavoriteColor"
    description = "Gets favorite colour."

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> llm.JsonObjectType:
        """Get the current date and time."""
        colors = ["red", "blue", "green", "yellow", "purple", "orange"]

        return {
            "success": True,
            "result": {
                "favorite_color": choice(colors),
            },
        }
