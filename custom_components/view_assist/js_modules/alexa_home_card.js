// Alexa Show style home screen card for View Assist.
//
// Load as a module resource alongside the other View Assist js_modules.
// Card config:
//   type: custom:va-alexa-home-card
//   weather_entity: weather.home   # optional, defaults to the View Assist device's weather entity
//   hour24: false                  # optional, defaults to the dashboard time format
//   show_date: true                # optional
//   buttons:                       # optional, right hand panel; scrolls when more than 3
//     - icon: mdi:lightbulb
//       name: Lights
//       tap_action:                # any standard Home Assistant action
//         action: call-service
//         service: light.toggle
//         target: { entity_id: light.lounge }
//
// All device data comes from window.viewassist.config (the StatusManager status).

const WEATHER_ICONS = {
  "clear-night": "mdi:weather-night",
  cloudy: "mdi:weather-cloudy",
  exceptional: "mdi:alert-circle-outline",
  fog: "mdi:weather-fog",
  hail: "mdi:weather-hail",
  lightning: "mdi:weather-lightning",
  "lightning-rainy": "mdi:weather-lightning-rainy",
  partlycloudy: "mdi:weather-partly-cloudy",
  pouring: "mdi:weather-pouring",
  rainy: "mdi:weather-rainy",
  snowy: "mdi:weather-snowy",
  "snowy-rainy": "mdi:weather-snowy-rainy",
  sunny: "mdi:weather-sunny",
  windy: "mdi:weather-windy",
  "windy-variant": "mdi:weather-windy-variant",
};

const STATUS_ICONS = {
  mic: "mdi:microphone-off",
  dnd: "mdi:minus-circle",
  hold: "mdi:pin",
  cycle: "mdi:rotate-right",
  mute: "mdi:volume-off",
};

const ACTIVE_STATUSES = ["running", "snoozed", "sounding"];

class VaAlexaHomeCard extends HTMLElement {
  constructor() {
    super();
    this._config = {};
    this._hass = null;
    this._interval = null;
    this._built = false;
    this._last = {};
  }

  setConfig(config) {
    this._config = config || {};
    this._last = {};
    if (this._built) this._renderButtons();
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
        .screen {
          position: relative;
          box-sizing: border-box;
          height: 100%;
          min-height: 300px;
          padding: 3vmin 4vmin;
          display: flex;
          flex-direction: row;
          gap: 3vmin;
          color: #fff;
          font-family: "Amazon Ember", Roboto, sans-serif;
          background: radial-gradient(ellipse at 30% 20%, #1c3b57 0%, #0b1622 70%);
          background-size: cover;
          background-position: center;
          border-radius: var(--ha-card-border-radius, 12px);
          overflow: hidden;
        }
        .main {
          flex: 1;
          min-width: 0;
          display: flex;
          flex-direction: column;
          justify-content: space-between;
        }
        .panel {
          --gap: 1.5vmin;
          --pad: 1.5vmin;
          --arrow: 4vmin;
          --icon: 8vmin;
          --font: 3vmin;
          --btn-h: calc(2 * var(--pad) + var(--icon) + 1vmin + 1.2 * var(--font));
          position: absolute;
          right: 4vmin;
          top: 50%;
          transform: translateY(-50%);
          box-sizing: border-box;
          width: 20vmin;
          padding: var(--arrow) var(--pad);
          border-radius: 4vmin;
          background: rgba(0, 0, 0, 0.5);
          --mdc-icon-size: var(--icon);
        }
        .panel[hidden] {
          display: none;
        }
        .buttons {
          height: calc(3 * var(--btn-h) + 2 * var(--gap));
          display: flex;
          flex-direction: column;
          gap: var(--gap);
          overflow-y: auto;
          scroll-snap-type: y mandatory;
          scrollbar-width: none;
        }
        .buttons::-webkit-scrollbar {
          display: none;
        }
        .arrow {
          position: absolute;
          left: 0;
          right: 0;
          height: var(--arrow);
          display: flex;
          align-items: center;
          justify-content: center;
          padding: 0;
          border: 0;
          background: none;
          color: inherit;
          cursor: pointer;
          --mdc-icon-size: 4vmin;
        }
        .arrow.up {
          top: 0;
        }
        .arrow.down {
          bottom: 0;
        }
        .arrow[hidden] {
          display: none;
        }
        .btn {
          flex: 0 0 var(--btn-h);
          scroll-snap-align: start;
          box-sizing: border-box;
          display: flex;
          flex-direction: column;
          align-items: center;
          justify-content: center;
          gap: 1vmin;
          padding: 0 1vmin;
          border: 0;
          border-radius: 3vmin;
          background: none;
          color: inherit;
          font: inherit;
          font-size: var(--font);
          line-height: 1.2;
          white-space: nowrap;
          cursor: pointer;
        }
        .btn span {
          max-width: 100%;
          overflow: hidden;
          text-overflow: ellipsis;
        }
        .btn:active {
          background: rgba(255, 255, 255, 0.2);
        }
        .top {
          display: flex;
          justify-content: space-between;
          align-items: flex-start;
          min-height: 8vmin;
        }
        .icons {
          display: flex;
          gap: 2vmin;
          align-items: center;
          --mdc-icon-size: 6vmin;
          opacity: 0.9;
        }
        .pill {
          display: flex;
          align-items: center;
          gap: 1.5vmin;
          padding: 1vmin 3vmin;
          border-radius: 999px;
          background: rgba(255, 255, 255, 0.18);
          backdrop-filter: blur(4px);
          font-size: 4.5vmin;
          font-variant-numeric: tabular-nums;
          --mdc-icon-size: 5vmin;
        }
        .pill.sounding {
          background: #e8505b;
          animation: pulse 1s ease-in-out infinite;
        }
        .pill {
          cursor: pointer;
        }
        .pill[hidden] {
          display: none;
        }
        .center {
          flex: 1;
          display: flex;
          flex-direction: column;
          align-items: center;
          justify-content: center;
          text-align: center;
        }
        .time {
          font-size: 28vmin;
          font-weight: 300;
          line-height: 1;
          letter-spacing: -0.02em;
        }
        .date {
          font-size: 6.5vmin;
          opacity: 0.8;
          margin-top: 1.5vmin;
        }
        .weather {
          display: flex;
          align-items: center;
          justify-content: center;
          gap: 2vmin;
          font-size: 6vmin;
          --mdc-icon-size: 9vmin;
        }
        .weather .cond {
          font-size: 4vmin;
          opacity: 0.75;
          text-transform: capitalize;
        }
        .weather[hidden] {
          display: none;
        }
        @keyframes pulse {
          50% { opacity: 0.6; }
        }
      </style>
      <div class="screen">
        <div class="main">
        <div class="top">
          <div class="icons" id="icons"></div>
          <div class="pill" id="pill" hidden>
            <ha-icon id="pill-icon" icon="mdi:alarm"></ha-icon>
            <span id="pill-text"></span>
          </div>
        </div>
        <div class="center">
          <div class="time" id="time"></div>
          <div class="date" id="date"></div>
        </div>
        <div class="weather" id="weather" hidden>
          <ha-icon id="w-icon"></ha-icon>
          <span id="w-temp"></span>
          <span class="cond" id="w-cond"></span>
        </div>
        </div>
        <div class="panel" id="panel" hidden>
          <button class="arrow up" id="up" hidden><ha-icon icon="mdi:chevron-up"></ha-icon></button>
          <div class="buttons" id="buttons"></div>
          <button class="arrow down" id="down" hidden><ha-icon icon="mdi:chevron-down"></ha-icon></button>
        </div>
      </div>
    `;
    this._els = {
      icons: root.getElementById("icons"),
      pill: root.getElementById("pill"),
      pillIcon: root.getElementById("pill-icon"),
      pillText: root.getElementById("pill-text"),
      time: root.getElementById("time"),
      date: root.getElementById("date"),
      weather: root.getElementById("weather"),
      wIcon: root.getElementById("w-icon"),
      wTemp: root.getElementById("w-temp"),
      wCond: root.getElementById("w-cond"),
      screen: root.querySelector(".screen"),
      panel: root.getElementById("panel"),
      buttons: root.getElementById("buttons"),
      up: root.getElementById("up"),
      down: root.getElementById("down"),
    };
    this._els.pill.addEventListener("click", () => this._openTimers());
    const { buttons, up, down } = this._els;
    const step = () => buttons.firstElementChild?.offsetHeight || buttons.clientHeight / 3;
    up.addEventListener("click", () => buttons.scrollBy({ top: -step(), behavior: "smooth" }));
    down.addEventListener("click", () => buttons.scrollBy({ top: step(), behavior: "smooth" }));
    buttons.addEventListener("scroll", () => this._updateArrows());
    this._renderButtons();
  }

  _renderButtons() {
    const buttons = this._config.buttons || [];
    this._els.panel.hidden = !buttons.length;
    this._els.buttons.replaceChildren(
      ...buttons.map((cfg) => {
        const btn = document.createElement("button");
        btn.className = "btn";
        if (cfg.icon) {
          const icon = document.createElement("ha-icon");
          icon.setAttribute("icon", cfg.icon);
          btn.appendChild(icon);
        }
        if (cfg.name) {
          const label = document.createElement("span");
          label.textContent = cfg.name;
          btn.appendChild(label);
        }
        btn.addEventListener("click", () => {
          this.dispatchEvent(
            new CustomEvent("hass-action", {
              bubbles: true,
              composed: true,
              detail: { config: cfg, action: "tap" },
            })
          );
        });
        return btn;
      })
    );
    requestAnimationFrame(() => this._updateArrows());
  }

  _openTimers() {
    const path = window.viewassist?.config?.config?.dashboard?.timers;
    if (!path || path.split(/[?#]/)[0] === window.location.pathname) return;
    history.pushState(null, "", path);
    window.dispatchEvent(new CustomEvent("location-changed"));
  }

  _updateArrows() {
    const { buttons, up, down } = this._els;
    up.hidden = buttons.scrollTop <= 1;
    down.hidden = buttons.scrollTop + buttons.clientHeight >= buttons.scrollHeight - 1;
  }

  _now() {
    const delta = window.viewassist?.server_time_delta || 0;
    return new Date(Date.now() + delta);
  }

  _setText(key, el, value) {
    if (this._last[key] === value) return;
    this._last[key] = value;
    el.textContent = value;
  }

  _update() {
    if (!this._built) return;
    const va = window.viewassist?.config;
    this._updateBackground(va);
    this._updateClock(va);
    this._updateIcons(va);
    this._updatePill(va);
    this._updateWeather(va);
  }

  _updateBackground(va) {
    const image = va?.background || "";
    if (this._last.background === image) return;
    this._last.background = image;
    const screen = this._els.screen;
    if (image) {
      const url = image.replaceAll('"', "%22");
      screen.style.backgroundImage = `linear-gradient(rgba(0, 0, 0, 0.35), rgba(0, 0, 0, 0.35)), url("${url}")`;
    } else {
      screen.style.backgroundImage = "";
    }
  }

  _updateClock(va) {
    const lang = document.documentElement.lang || navigator.language || "en-GB";
    const now = this._now();
    const hour24 =
      this._config.hour24 ??
      va?.config?.dashboard?.display_settings?.time_format === "hour_24";
    this._setText(
      "time",
      this._els.time,
      new Intl.DateTimeFormat(lang, {
        hour: "numeric",
        minute: "2-digit",
        hour12: !hour24,
      }).format(now)
    );
    const dateText =
      this._config.show_date === false
        ? ""
        : new Intl.DateTimeFormat(lang, {
          weekday: "long",
          month: "long",
          day: "numeric",
        }).format(now);
    this._setText("date", this._els.date, dateText);
  }

  _activeTimers(va) {
    return (va?.timers || []).filter((t) => ACTIVE_STATUSES.includes(t.status));
  }

  _updateIcons(va) {
    const icons = [];
    const timers = this._activeTimers(va);
    if (timers.some((t) => t.timer_class === "alarm")) icons.push("mdi:alarm");
    if (timers.some((t) => t.timer_class === "timer")) icons.push("mdi:timer-outline");
    if (timers.some((t) => t.timer_class === "reminder")) icons.push("mdi:calendar-clock-outline");
    if (va?.muted) icons.push(STATUS_ICONS.mic);
    if (va?.hold) icons.push(STATUS_ICONS.hold);
    if (va?.mode === "cycle") icons.push(STATUS_ICONS.cycle);

    const key = icons.join("|");
    if (this._last.icons === key) return;
    this._last.icons = key;
    this._els.icons.replaceChildren(
      ...icons.map((icon) => {
        const el = document.createElement("ha-icon");
        el.setAttribute("icon", icon);
        return el;
      })
    );
  }

  _updatePill(va) {
    const { pill, pillIcon, pillText } = this._els;
    const now = this._now().getTime();
    const candidates = this._activeTimers(va)
      .filter((t) => t.timer_class === "alarm" || t.timer_class === "timer")
      .map((t) => ({ t, expires: new Date(t.timer_info?.expires_at).getTime() }))
      .filter((c) => !Number.isNaN(c.expires))
      .sort((a, b) => a.expires - b.expires);

    const sounding = candidates.find((c) => c.t.status === "sounding" || c.expires <= now);
    const next = sounding || candidates[0];
    if (!next) {
      pill.hidden = true;
      return;
    }

    pill.hidden = false;
    pill.classList.toggle("sounding", Boolean(sounding));
    pillIcon.setAttribute(
      "icon",
      next.t.timer_class === "alarm" ? "mdi:alarm" : "mdi:timer-outline"
    );
    pillText.textContent = this._formatCountdown(next.expires - now);
  }

  _formatCountdown(ms) {
    const total = Math.abs(Math.round(ms / 1000));
    const days = Math.floor(total / 86400);
    const h = String(Math.floor((total % 86400) / 3600)).padStart(2, "0");
    const m = String(Math.floor((total % 3600) / 60)).padStart(2, "0");
    const s = String(total % 60).padStart(2, "0");
    const sign = ms < 0 ? "-" : "";
    return days ? `${sign}${days}d ${h}:${m}:${s}` : `${sign}${h}:${m}:${s}`;
  }

  _updateWeather(va) {
    const entityId = this._config.weather_entity || va?.config?.default?.weather_entity;
    const state = entityId ? this._hass?.states?.[entityId] : null;
    const { weather, wIcon, wTemp, wCond } = this._els;
    if (!state) {
      weather.hidden = true;
      return;
    }
    weather.hidden = false;
    const temp = state.attributes.temperature;
    const unit =
      state.attributes.temperature_unit || this._hass.config?.unit_system?.temperature || "";
    const icon = WEATHER_ICONS[state.state] || "mdi:weather-cloudy";
    if (this._last.wIcon !== icon) {
      this._last.wIcon = icon;
      wIcon.setAttribute("icon", icon);
    }
    this._setText("wTemp", wTemp, temp !== undefined ? `${Math.round(temp)}${unit}` : "");
    this._setText("wCond", wCond, state.state.replace(/[-_]/g, " "));
  }
}

if (!customElements.get("va-alexa-home-card")) {
  customElements.define("va-alexa-home-card", VaAlexaHomeCard);
}

window.customCards = window.customCards || [];
window.customCards.push({
  type: "va-alexa-home-card",
  name: "View Assist Alexa Home",
  description: "Alexa Show style home screen with clock, weather, alarm icons and countdown pill.",
});
