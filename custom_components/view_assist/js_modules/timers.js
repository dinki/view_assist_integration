// Generate timer cards list to display timers

class TimerCards {
    noTimersCard() {
        return {
            "type": "custom:button-card",
            "name": "No timers",
            "styles": {
                "card": [
                    { "background-color": "transparent" },
                    { "font-size": "20vh" },
                    { "color": "white" },
                    { "height": "75vh" },
                ],

            }
        };
    }

    timerCards(show_all = false) {
        const entity_id = localStorage.getItem("view_assist_sensor");
        const cards = Array();

        // If not timers return empty list
        if (!window.viewassist?.config?.timers) return cards;

        window.viewassist.config?.timers.forEach((timer) => {
            // If not matchin supplied entity id, skip this timer
            if (!show_all && entity_id && timer.entity_id != entity_id) return;

            let name = timer.name;
            if (!name && timer.timer_type == 'interval') {
                name = timer.duration
            }

            let expiry_time = timer.expiry.time;
            if (timer.expiry.day != "Today") {
                expiry_time = `<div style="padding: 0; line-height: 4vw">${timer.expiry.time}</br><span style="padding: 0; font-size: 3vw; text-align: right">${timer.expiry.day}</span></div>`;
            }

            const cancelButton = {
                "type": "custom:button-card",
                "name": "Cancel",
                "icon": "mdi:alarm-off",
                "show_name": false,
                "tap_action": {
                    "action": "call-service",
                    "service": "view_assist.cancel_timer",
                    "service_data": {
                        "timer_id": timer.id
                    }
                },
                "styles": {
                    "card": [
                        { "justify-self": "end" },
                        { "align-self": "center" },
                        { "border-radius": "0 10px 10px 0" },
                        { "background-color": "var(--red-color)" },
                        { "height": "20vh" },
                        { "width": "10vw" },
                    ],
                    "icon": [
                        { "color": "white" }
                    ]
                }
            };

            const dismissButton = {
                "type": "custom:button-card",
                "name": "Dismiss",
                "icon": "mdi:bell-off",
                "show_name": false,
                "tap_action": {
                    "action": "call-service",
                    "service": "view_assist.broadcast_event",
                    "service_data": {
                        "event_name": "viewassist",
                        "event_data": {
                            "command": "dismiss alarm",
                            "entity_id": timer.entity_id,
                            "timer_id": timer.id,
                        }
                    }
                },
                "styles": {
                    "card": [
                        { "justify-self": "end" },
                        { "align-self": "center" },
                        { "border-radius": "0 10px 10px 0" },
                        { "background-color": "var(--red-color)" },
                        { "height": "20vh" },
                        { "width": "10vw" },
                    ],
                    "icon": [
                        { "color": "white" }
                    ]
                }
            };

            const snoozeButton = {
                "type": "custom:button-card",
                "name": "Snooze",
                "icon": "mdi:alarm-snooze",
                "show_name": false,
                "tap_action": {
                    "action": "call-service",
                    "service": "view_assist.broadcast_event",
                    "service_data": {
                        "event_name": "viewassist",
                        "event_data": {
                            "command": "snooze alarm",
                            "entity_id": timer.entity_id,
                            "timer_id": timer.id,
                        }
                    }
                },
                "styles": {
                    "card": [
                        { "justify-self": "end" },
                        { "align-self": "center" },
                        { "background-color": "var(--blue-color)" },
                        { "height": "20vh" },
                        { "width": "10vw" },
                    ],
                    "icon": [
                        { "color": "white" }
                    ]
                }
            };

            const timerCard = {
                "type": "custom:button-card",
                    "name": name,
                "styles": {
                    "grid": [
                        { "grid-template-areas": (timer.status == "expired" ? "'n time snooze cancel'" : "'n time cancel'") },
                        { "grid-template-rows": "1fr" },
                        { "grid-template-columns": (timer.status == "expired" ? "40% 35% 14% 11%" : "45% 44% 11%") }
                    ],
                    "card": [
                        { "background-color": "rgba(0, 0, 0, 0.7)" },
                        { "border-radius": "3vh" },
                        { "height": "20%" },
                        { "padding": "0" },
                        { "justify-self": "center" },
                        { "width": "90vw"}
                    ],
                    "name": [
                        { "color": "white" },
                        { "font-size": "3vw" },
                        { "justify-self": "start" },
                        { "text-align": "left" },
                        { "padding-left": "2vw" },
                    ],
                    "custom_fields": {
                        "time": [
                            { "color": "white" },
                            { "font-size": "5vw" },
                            { "justify-self": "end" },
                            { "padding-right": "3vw" }
                        ],
                        "cancel": [
                            { "justify-self": "end" },
                            { "padding-left": "0" }
                        ],
                        "snooze": [
                            { "justify-self": "end" },
                            { "padding-right": "0" }
                        ]
                    }
                },
                "custom_fields": {
                    "time": (timer.timer_type == 'interval') ? `<viewassist-countdown expires='${timer.expires}'></viewassist-countdown>` : expiry_time,
                    ...(timer.status == 'expired') ? {
                        "snooze": { "card": snoozeButton },
                        "cancel": { "card": dismissButton }
                    } : {
                        "cancel": { "card": cancelButton }
                    },
                }
            };
            cards.push(timerCard);
        });
        return {
            "type": "custom:layout-card",
            "layout_type": "custom:vertical-layout",
            "layout": {
                "max_cols": 3
            },
            "cards": cards
        };
    }

    singleTimerCard(timer_id) {
        const entity_id = localStorage.getItem("view_assist_sensor");
        let card = null;

        // If not timers return empty list
        if (!window.viewassist?.config?.timers) return card;

        const timer = window.viewassist.config?.timers.find(t => t.id === timer_id && (t.entity_id === entity_id || !entity_id));
        if (!timer) return card;

        let name = timer.name;
        if (!name && timer.timer_type == 'interval') {
            name = timer.duration
        }


        function actionButton(action_name, width, background_colour, text_colour, tap_action, display) {
            return {
                "type": "custom:button-card",
                "name": action_name,
                "tap_action": tap_action,
                "styles": {
                    "card": [
                        { "justify-content": "center" },
                        { "align-items": "center" },
                        { "width": width },
                        { "border-radius": "6vh" },
                        { "height": "12vh" },
                        { "border": "none" },
                        { "background-color": background_colour },
                        { "display": (display) ? "flex" : "none" }
                    ],
                    "name": [
                        { "font-size": "5vh" },
                        { "color": text_colour },
                        { "font-weight": "bold" },
                        { "text-align": "center" }
                    ]
                }
            }
        };

        const timerCard = {
            "type": "custom:button-card",
            "styles": {
                "grid": [
                    { "grid-template-areas": "'display_timer_name' 'time' 'day' 'action_buttons'" },
                    { "grid-template-rows": "0.1fr min-content min-content min-content" }
                ],
                "card": [
                    { "background-color": "transparent" },
                    { "width": "100%" },
                    { "padding": "0" },
                    { "justify-content": "center" },
                    { "align-items": "center" }
                ],
                "custom_fields": {
                    "display_timer_name": [
                        { "display": "grid" },
                        { "justify-self": "center" },
                        { "z-index": "1" },
                        { "font-size": "6vw" },
                        { "color": "white" },
                        { "padding-top": "5vh" },
                        { "padding-bottom": "3vh" },

                    ],
                    "time": [
                        { "display": "grid" },
                        { "justify-self": "center" },
                        { "z-index": "1" },
                        { "font-size": "21vw" },
                        { "height": "21vw" },
                        { "font-weight": "bold" },
                        { "padding-bottom": "0" },
                        { "color": "white" },
                        { "width": "100%" }
                    ],
                    "day": [
                        { "display": "grid" },
                        { "justify-self": "center" },
                        { "z-index": "1" },
                        { "font-size": "10vh" },
                        { "height": "14vh" },
                        { "font-weight": "bold" },
                        { "color": "white" },
                        { "width": "100%" }
                    ],
                    "action_buttons": [
                        { "justify-items": "center" },
                        { "z-index": "1" },
                        { "width": "100%" },
                        { "padding": "0" }
                    ],
                },
            },
            "custom_fields": {
                "display_timer_name": name,
                "time": (timer.timer_type == 'interval') ? `<viewassist-countdown expires='${timer.expires}'></viewassist-countdown>` : timer.expiry.time,
                "day": (timer.timer_type == 'interval') ? '' : (timer.expiry.day != "Today") ? timer.expiry.day : '',
                "action_buttons": {
                    "card": {
                        "type": "custom:button-card",
                        "styles": {
                            "grid": [
                                { "grid-template-areas": "'snooze dismiss' 'cancel cancel'" },
                                { "grid-template-columns": "1fr 1fr" },
                            ],
                            "card": [
                                { "background-color": "transparent" },
                                { "border": "none" },
                                { "padding-top": "3vh" },
                                { "width": "100%" },
                            ],
                            "custom_fields": {
                                "snooze": [
                                    { "justify-self": "center" },
                                    { "align-self": "center" },
                                    { "z-index": "1" },
                                    { "font-size": "15vh" },
                                    { "display": "grid" },
                                    { "padding-right": "5vw" },
                                ],
                                "dismiss": [
                                    { "justify-self": "center" },
                                    { "align-self": "center" },
                                    { "z-index": "1" },
                                    { "font-size": "15vh" },
                                    { "display": "grid" },
                                    { "padding-left": "5vw" },
                                ],
                                "cancel": [
                                    { "justify-self": "center" },
                                    { "align-self": "center" },
                                    { "z-index": "1" },
                                    { "font-size": "15vh" },
                                    { "width": "max-content" },
                                    { "display": "grid" },
                                ]
                            }
                        },
                        "custom_fields": {
                            "snooze": {
                                "card": actionButton("Snooze", "35vw", "white", "black", {
                                    "action": "call-service",
                                    "service": "view_assist.broadcast_event",
                                    "service_data": {
                                        "event_name": "viewassist",
                                        "event_data": {
                                            "command": "snooze alarm",
                                            "entity_id": entity_id,
                                            "timer_id": timer.id,
                                        }
                                    }
                                },
                                    timer.status == 'expired'
                                )
                            },
                            "dismiss": {
                                "card": actionButton("Dismiss", "35vw", "#2899f3", "white", {
                                    "action": "call-service",
                                    "service": "view_assist.broadcast_event",
                                    "service_data": {
                                        "event_name": "viewassist",
                                        "event_data": {
                                            "command": "dismiss alarm",
                                            "entity_id": entity_id,
                                            "timer_id": timer.id,
                                        }
                                    }
                                },
                                    timer.status == 'expired'
                                ),
                            },
                            "cancel": {
                                "card": actionButton("Cancel", "40vw", "var(--red-color)", "white", {
                                    "action": "call-service",
                                    "service": "view_assist.cancel_timer",
                                    "service_data": {
                                        "timer_id": timer.id
                                    }
                                },
                                    timer.status != 'expired'
                                )
                            }
                        }
                    }
                }
            }
        };
        return timerCard
    }
}

export const timerCards = new TimerCards();
