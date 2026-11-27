// View Assist clock-alt view (JS version of the button-card clock-alt).
//
// Load as a module resource alongside the other View Assist js_modules.
// Card config:
//   type: custom:va-clock-alt-card
//   satellite_entity: assist_satellite.kitchen   # optional, defaults to the View Assist satellite
//   dashboard: /view-assist                      # optional, defaults to the satellite's dashboard
//   hour24: false                                # optional, defaults to the satellite's use_24_hour_time
//
// Tap: hold mode. Double tap: normal mode. Long press: toggle the mic mute switch.
// Tap the weather: opens the weather view.

const WEATHER_ICONS = {
  "clear-night": "mdi:weather-night",
  "cloudy": "mdi:weather-cloudy",
  "dust": "mdi:weather-dust",
  "fog": "mdi:weather-fog",
  "hail": "mdi:weather-hail",
  "hazy": "mdi:weather-hazy",
  "hurricane": "mdi:weather-hurricane",
  "lightning": "mdi:weather-lightning",
  "lightning-rainy": "mdi:weather-lightning-rainy",
  "partlycloudy": "mdi:weather-partly-cloudy",
  "partly-lightning": "mdi:weather-partly-lightning",
  "partly-rainy": "mdi:weather-partly-rainy",
  "partly-snowy": "mdi:weather-partly-snowy",
  "partly-snowy-rainy": "mdi:weather-partly-snowy-rainy",
  "pouring": "mdi:weather-pouring",
  "rainy": "mdi:weather-rainy",
  "snowy": "mdi:weather-snowy",
  "snowy-heavy": "mdi:weather-snowy-heavy",
  "snowy-rainy": "mdi:weather-snowy-rainy",
  "sunny": "mdi:weather-sunny",
  "tornado": "mdi:weather-tornado",
  "windy": "mdi:weather-windy",
  "windy-variant": "mdi:weather-windy-variant",
};

class VaClockAltCard extends HTMLElement {
  constructor() {
    super();
    this._config = {};
    this._hass = null;
    this._interval = null;
    this._built = false;
    this._last = {};
    this._pressTimer = null;
    this._tapTimer = null;
    this._tapCount = 0;
    this._held = false;
  }

  setConfig(config) {
    this._config = config || {};
    this._last = {};
  }

  set hass(hass) {
    this._hass = hass;
    this._update();
  }

  getCardSize() {
    return 6;
  }

  connectedCallback() {
    this._build();
    this._update();
    this._interval = setInterval(() => this._update(), 1000);
  }

  disconnectedCallback() {
    clearInterval(this._interval);
    this._interval = null;
    clearTimeout(this._pressTimer);
    clearTimeout(this._tapTimer);
  }

  _build() {
    if (this._built) return;
    this._built = true;
    const root = this.attachShadow({ mode: "open" });
    root.innerHTML = `
      <style>
        :host {
          display: block;
          height: 100%;
        }
        [hidden] {
          display: none !important;
        }
        .card {
          position: relative;
          box-sizing: border-box;
          height: 100%;
          min-height: 300px;
          overflow: hidden;
          background-color: black;
          background-position: center;
          background-size: cover;
          background-repeat: no-repeat;
          color: #fff;
          font-weight: 300;
          border-radius: var(--ha-card-border-radius, 12px);
          user-select: none;
          -webkit-user-select: none;
          touch-action: manipulation;
        }
        .clock {
          position: absolute;
          bottom: 3%;
          left: 0;
          display: flex;
          align-items: flex-end;
          width: max-content;
          max-width: 100%;
          padding-left: 2%;
          z-index: 1;
        }
        .time {
          font-size: 20vh;
          font-weight: 300;
          line-height: 1;
          white-space: nowrap;
          text-shadow: 2px 2px black;
          color: #fff;
        }
        .weather {
          display: flex;
          align-items: center;
          gap: 1vh;
          margin-left: 2vh;
          margin-bottom: 1vh;
          cursor: pointer;
        }
        .weather[hidden] {
          display: none;
        }
        .w-icon {
          --mdc-icon-size: 12vh;
          flex: 0 0 auto;
          filter: drop-shadow(2px 2px black);
        }
        .w-temp {
          font-size: 8vh;
          font-weight: 300;
          white-space: nowrap;
          text-shadow: 2px 2px black;
        }
      </style>
      <div class="card" id="card">
        <div class="clock">
          <div class="time" id="time"></div>
          <div class="weather" id="weather" hidden>
            <ha-icon class="w-icon" id="w-icon"></ha-icon>
            <span class="w-temp" id="w-temp"></span>
          </div>
        </div>
      </div>
    `;
    this._els = {
      card: root.getElementById("card"),
      time: root.getElementById("time"),
      weather: root.getElementById("weather"),
      wIcon: root.getElementById("w-icon"),
      wTemp: root.getElementById("w-temp"),
    };

    const card = this._els.card;
    card.addEventListener("pointerdown", () => this._onPressStart());
    card.addEventListener("pointerup", () => this._onPressEnd());
    card.addEventListener("pointerleave", () => this._onPressCancel());
    card.addEventListener("pointercancel", () => this._onPressCancel());

    const weather = this._els.weather;
    ["pointerdown", "pointerup", "click"].forEach((evt) =>
      weather.addEventListener(evt, (e) => {
        e.stopPropagation();
        if (evt === "click") this._openWeather();
      })
    );
  }

  _satEntityId() {
    return (
      this._config.satellite_entity ||
      localStorage.getItem("view_assist_sensor") ||
      ""
    );
  }

  _satState() {
    const id = this._satEntityId();
    return id ? this._hass?.states?.[id] : null;
  }

  _setText(key, el, value) {
    if (this._last[key] === value) return;
    this._last[key] = value;
    el.textContent = value;
  }

  _setHTML(key, el, value) {
    if (this._last[key] === value) return;
    this._last[key] = value;
    el.innerHTML = value;
  }

  _update() {
    if (!this._built || !this._hass) return;
    const sat = this._satState();
    const attrs = sat?.attributes || {};
    const { card } = this._els;

    card.style.fontFamily = `"${attrs.font_style || "Roboto"}", sans-serif`;

    this._updateBackground(attrs.background);
    this._updateClock(attrs);
    this._updateWeather(attrs);
  }

  _updateBackground(background) {
    const image = background || "";
    if (this._last.background === image) return;
    this._last.background = image;
    this._els.card.style.background = image
      ? `center / cover no-repeat url(${image})`
      : "";
  }

  _updateClock(attrs) {
    const hour24 = this._config.hour24 ?? attrs.use_24_hour_time ?? false;
    this._setHTML(
      "time",
      this._els.time,
      `<viewassist-clock server_time=true mode='time' hour24=${hour24}></viewassist-clock>`
    );
  }

  _updateWeather(attrs) {
    const entityId = attrs.weather_entity;
    const state = entityId ? this._hass?.states?.[entityId] : null;
    const { weather, wIcon, wTemp } = this._els;

    if (!state) {
      weather.hidden = true;
      return;
    }
    weather.hidden = false;

    const icon = WEATHER_ICONS[state.state] || "mdi:help";
    if (this._last.wIcon !== icon) {
      this._last.wIcon = icon;
      wIcon.setAttribute("icon", icon);
    }

    const temp = state.attributes.temperature;
    this._setText("wTemp", wTemp, temp !== undefined ? `${temp}°` : "");
  }

  _onPressStart() {
    this._held = false;
    clearTimeout(this._pressTimer);
    this._pressTimer = setTimeout(() => {
      this._held = true;
      this._tapCount = 0;
      clearTimeout(this._tapTimer);
      this._toggleMic();
    }, 500);
  }

  _onPressEnd() {
    clearTimeout(this._pressTimer);
    if (this._held) return;
    this._tapCount++;
    clearTimeout(this._tapTimer);
    this._tapTimer = setTimeout(() => {
      const count = this._tapCount;
      this._tapCount = 0;
      this._setMode(count >= 2 ? "normal" : "hold");
    }, 250);
  }

  _onPressCancel() {
    clearTimeout(this._pressTimer);
    clearTimeout(this._tapTimer);
    this._tapCount = 0;
    this._held = false;
  }

  _setMode(mode) {
    const sat = this._satEntityId();
    if (!sat) return;
    this._hass.callService("view_assist", "set_state", {
      entity_id: sat,
      mode,
    });
  }

  _toggleMic() {
    const micSwitch = this._satState()?.attributes?.mute_switch;
    if (!micSwitch) return;
    this._hass.callService("switch", "toggle", { entity_id: micSwitch });
  }

  _openWeather() {
    const sat = this._satEntityId();
    if (!sat) return;
    const dashboard =
      this._config.dashboard ||
      this._satState()?.attributes?.dashboard ||
      "/view-assist";
    this._hass.callService("view_assist", "navigate", {
      device: sat,
      path: `${dashboard}/weather`,
    });
  }
}

if (!customElements.get("va-clock-alt-card")) {
  customElements.define("va-clock-alt-card", VaClockAltCard);
}

window.customCards = window.customCards || [];
window.customCards.push({
  type: "va-clock-alt-card",
  name: "View Assist Clock Alt",
  description: "Large clock with weather beside it, anchored near the bottom of the card.",
});