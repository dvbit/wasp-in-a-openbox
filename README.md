# Wasp in a Openbox

Enclosed and open-space occupancy helpers for Home Assistant.

Extends the classic "wasp in a box" occupancy pattern with **border sensor** support for open-plan rooms that have no physical door — archways, curtain passages, and open corridors tracked by curtain-shaped PIR sensors.

## Features

- **Classic door mode** — the original wasp-in-a-box logic, unchanged.
- **Border sensor mode** — occupancy via curtain PIR + room motion correlation.
- **Mixed mode** — rooms with both a door and open passages.
- **Direction inference** — entry/exit detection based on border + motion timing.
- **Multi-language UI** — English, Italian, French, Spanish, German, Dutch, Portuguese.
- **Services** — `reset`, `force_entry`, `force_exit`.
- **Backward compatible** — existing v1.1 entries auto-migrate to v1.2.

## Requirements

- Home Assistant **2026.1** or later.
- HACS (recommended) or manual installation.

## Installation

### HACS (recommended)

1. Open HACS → Integrations → three-dot menu → Custom repositories.
2. Add `https://github.com/dvbit/HA-Wasp-In-A-Openbox` as an Integration.
3. Install **Wasp in a Openbox**.
4. Restart Home Assistant.
5. Go to Settings → Helpers → + → **Wasp in a Openbox**.

### Manual

1. Copy `custom_components/wasp_in_a_openbox/` to your `config/custom_components/` folder.
2. Restart Home Assistant.
3. Go to Settings → Helpers → + → **Wasp in a Openbox**.

## Configuration Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `name` | text | — | Name for the helper entity |
| `wasp_id` | entity | — | Room motion sensor (required) |
| `box_id` | entity | — | Door contact sensor (optional) |
| `door_closed_delay` | number | 30 | Seconds after door closes before evaluating occupancy |
| `door_open_timeout` | number | 300 | Seconds of no motion with door open before unoccupied |
| `immediate_on` | boolean | true | Activate immediately on motion or door open |
| `border_ids` | entity list | [] | Curtain PIR / passage sensors |
| `border_correlation_window` | number | 5 | Seconds to confirm entry after border trigger |
| `border_exit_timeout` | number | 30 | Seconds after border crossing to confirm exit |
| `border_only_mode` | boolean | false | Room has no door — rely on border + motion only |

## Usage Examples

### Example 1: Classic bathroom (door only)

```yaml
# Settings → Helpers → Wasp in a Openbox
# Name: Bathroom Occupancy
# Motion sensor: binary_sensor.bathroom_motion
# Door sensor: binary_sensor.bathroom_door
# Door closed delay: 25
# Door open timeout: 300
# Immediate on: true
# Border sensors: (leave empty)
# Border only mode: false
```

Automation to control lights:

```yaml
automation:
  - alias: "Bathroom light on occupancy"
    trigger:
      - platform: state
        entity_id: binary_sensor.bathroom_occupancy
        to: "on"
    action:
      - action: light.turn_on
        target:
          entity_id: light.bathroom

  - alias: "Bathroom light off on vacancy"
    trigger:
      - platform: state
        entity_id: binary_sensor.bathroom_occupancy
        to: "off"
        for:
          minutes: 2
    action:
      - action: light.turn_off
        target:
          entity_id: light.bathroom
```

### Example 2: Open-plan kitchen (border only)

```yaml
# Name: Kitchen Occupancy
# Motion sensor: binary_sensor.kitchen_motion
# Door sensor: (leave empty)
# Border sensors: binary_sensor.hallway_kitchen_curtain_pir
# Border correlation window: 8
# Border exit timeout: 45
# Border only mode: true
```

### Example 3: Bedroom with door + open arch to ensuite (mixed)

```yaml
# Name: Bedroom Occupancy
# Motion sensor: binary_sensor.bedroom_motion
# Door sensor: binary_sensor.bedroom_door
# Border sensors: binary_sensor.bedroom_ensuite_curtain_pir
# Border only mode: false
```

### Example 4: Using force_entry / force_exit services

```yaml
automation:
  - alias: "Force kitchen occupied via button"
    trigger:
      - platform: event
        event_type: zha_event
        event_data:
          device_id: "abc123"
          command: "single"
    action:
      - action: wasp_in_a_openbox.force_entry
        target:
          entity_id: binary_sensor.kitchen_occupancy
```

## Exposed Attributes

| Attribute | Values | Description |
|---|---|---|
| `motion_sensor_state` | on / off / unknown | Current motion sensor state |
| `door_sensor_state` | on / off / none / unknown | Door sensor state |
| `occupancy_source` | door / border / motion / manual / none | What determined current state |
| `border_sensors_state` | dict | Last triggered timestamp per border sensor |
| `last_border_event` | ISO timestamp | Most recent border crossing |
| `inferred_direction` | entry / exit / unknown | Direction of last crossing |
| `fully_enclosed` | true / false | Room has door only, no borders |

## Specification

The following is the original specification used to implement this integration.

### Problem Statement

The classic Wasp in a Box works for enclosed rooms with a door. But most homes also have open-plan transitions (archways, curtain passages, corridors). A curtain-shaped PIR can detect someone crossing, but it produces transient events rather than persistent state — it cannot "seal" the room.

### Sensor Taxonomy

| Type | Config key | Behavior |
|---|---|---|
| Door sensor | `box_id` | Binary open/closed. Seals the room. |
| Border sensor | `border_ids` | Transient PIR trigger on passage. No seal. |
| Room motion sensor | `wasp_id` | Detects motion inside the room. |

### Border Sensor Logic

- **Entry inference (§3.1):** Border PIR fires → room motion follows within `border_correlation_window` → entry confirmed.
- **Exit inference (§3.2):** Room motion clear → border PIR fires → no motion resumes within `border_exit_timeout` → exit confirmed.
- **Pass-through:** Border fires with no subsequent room motion → transient, no occupancy change.

### State Machine Modes

- **Door + borders (§4.1):** Door closed + motion → occupied (classic). Door open + border entry → occupied. Door logic takes priority.
- **Border-only (§4.2):** Border + motion correlation → occupied. Motion clear for exit timeout → unoccupied.

### Edge Cases (§7)

- Multiple border sensors debounced within 2s.
- Same border PIR referenced by two rooms evaluates independently.
- Mixed boundaries: `fully_enclosed: false` when borders are present.
- PIR cooldown: `border_correlation_window` should exceed PIR hardware cooldown.
- HA restart: current states replayed, border history set to unknown until first event.

### Services (§8)

- `reset` — clear occupancy, timers, border history.
- `force_entry` — manually set occupied.
- `force_exit` — manually set unoccupied.

### Backward Compatibility (§9)

All new fields are optional with defaults that produce identical behavior to the original integration. Config entries auto-migrate from v1.1 to v1.2.

## License

MIT — see [LICENSE](LICENSE).
