export async function hass_base_el() {
    await Promise.race([
      customElements.whenDefined("home-assistant"),
      customElements.whenDefined("hc-main"),
    ]);

    const element = customElements.get("home-assistant")
      ? "home-assistant"
      : "hc-main";

    while (!document.querySelector(element))
      await new Promise((r) => window.setTimeout(r, 100));
    return document.querySelector(element);
  }

export async function provideHass() {
const base = await hass_base_el();
while (!base.hass) await new Promise((r) => window.setTimeout(r, 100));
return base.hass;
}

const version = "1.0.0"
const hass = await provideHass();

// Get the view asssit entity for this browser id and save in local storage
const va_entity = await hass.callWS({
    type: 'view_assist/get_entity_id',
    browser_id: localStorage.getItem("browser_mod-browser-id")
})
localStorage.setItem("view_assist_sensor", va_entity);

// Output to console that this javascript is loaded
console.info(
    `%cVIEW ASSIST ${version} IS INSTALLED
  %cView Assist Entity: ${va_entity}`,
    "color: green; font-weight: bold",
    ""
  );
