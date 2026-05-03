# Wasp in a Openbox

Helper per il rilevamento di occupazione in spazi chiusi e aperti per Home Assistant.

Estende il classico pattern "wasp in a box" con il supporto ai **sensori di confine** per stanze open-plan senza porta fisica — archi, passaggi con tenda e corridoi aperti tracciati da sensori PIR a tenda.

## Funzionalità

- **Modalità porta classica** — la logica originale wasp-in-a-box, invariata.
- **Modalità sensore di confine** — occupazione tramite correlazione PIR a tenda + movimento stanza.
- **Modalità mista** — stanze con sia una porta che passaggi aperti.
- **Inferenza direzione** — rilevamento ingresso/uscita basato su temporizzazione confine + movimento.
- **UI multilingua** — Inglese, Italiano, Francese, Spagnolo, Tedesco, Olandese, Portoghese.
- **Servizi** — `reset`, `force_entry`, `force_exit`.
- **Retrocompatibile** — le voci v1.1 esistenti migrano automaticamente a v1.2.

## Requisiti

- Home Assistant **2026.1** o successivo.
- HACS (consigliato) o installazione manuale.

## Installazione

### HACS (consigliato)

1. Apri HACS → Integrazioni → menu tre punti → Repository personalizzate.
2. Aggiungi `https://github.com/dvbit/HA-Wasp-In-A-Openbox` come Integrazione.
3. Installa **Wasp in a Openbox**.
4. Riavvia Home Assistant.
5. Vai in Impostazioni → Helper → + → **Wasp in a Openbox**.

### Manuale

1. Copia `custom_components/wasp_in_a_openbox/` nella cartella `config/custom_components/`.
2. Riavvia Home Assistant.
3. Vai in Impostazioni → Helper → + → **Wasp in a Openbox**.

## Parametri di Configurazione

| Parametro | Tipo | Default | Descrizione |
|---|---|---|---|
| `name` | testo | — | Nome per l'entità helper |
| `wasp_id` | entità | — | Sensore di movimento stanza (obbligatorio) |
| `box_id` | entità | — | Sensore contatto porta (opzionale) |
| `door_closed_delay` | numero | 30 | Secondi dopo la chiusura della porta prima di valutare l'occupazione |
| `door_open_timeout` | numero | 300 | Secondi senza movimento con porta aperta prima di considerare non occupato |
| `immediate_on` | booleano | true | Attiva immediatamente al movimento o apertura porta |
| `border_ids` | lista entità | [] | Sensori PIR a tenda / di passaggio |
| `border_correlation_window` | numero | 5 | Secondi per confermare ingresso dopo trigger confine |
| `border_exit_timeout` | numero | 30 | Secondi dopo attraversamento confine per confermare uscita |
| `border_only_mode` | booleano | false | Stanza senza porta — basarsi solo su confine + movimento |

## Esempi di Utilizzo

### Esempio 1: Bagno classico (solo porta)

```yaml
# Impostazioni → Helper → Wasp in a Openbox
# Nome: Occupazione Bagno
# Sensore di movimento: binary_sensor.bagno_movimento
# Sensore porta: binary_sensor.bagno_porta
# Ritardo porta chiusa: 25
# Timeout porta aperta: 300
# Attivazione immediata: true
# Sensori di confine: (lascia vuoto)
# Modalità solo confine: false
```

Automazione per controllare le luci:

```yaml
automation:
  - alias: "Luce bagno su occupazione"
    trigger:
      - platform: state
        entity_id: binary_sensor.occupazione_bagno
        to: "on"
    action:
      - action: light.turn_on
        target:
          entity_id: light.bagno

  - alias: "Luce bagno spenta su non occupazione"
    trigger:
      - platform: state
        entity_id: binary_sensor.occupazione_bagno
        to: "off"
        for:
          minutes: 2
    action:
      - action: light.turn_off
        target:
          entity_id: light.bagno
```

### Esempio 2: Cucina open-plan (solo confine)

```yaml
# Nome: Occupazione Cucina
# Sensore di movimento: binary_sensor.cucina_movimento
# Sensore porta: (lascia vuoto)
# Sensori di confine: binary_sensor.corridoio_cucina_pir_tenda
# Finestra di correlazione confine: 8
# Timeout uscita confine: 45
# Modalità solo confine: true
```

### Esempio 3: Camera con porta + arco aperto verso bagno (mista)

```yaml
# Nome: Occupazione Camera
# Sensore di movimento: binary_sensor.camera_movimento
# Sensore porta: binary_sensor.camera_porta
# Sensori di confine: binary_sensor.camera_bagno_pir_tenda
# Modalità solo confine: false
```

### Esempio 4: Uso dei servizi force_entry / force_exit

```yaml
automation:
  - alias: "Forza occupazione cucina tramite pulsante"
    trigger:
      - platform: event
        event_type: zha_event
        event_data:
          device_id: "abc123"
          command: "single"
    action:
      - action: wasp_in_a_openbox.force_entry
        target:
          entity_id: binary_sensor.occupazione_cucina
```

## Attributi Esposti

| Attributo | Valori | Descrizione |
|---|---|---|
| `motion_sensor_state` | on / off / unknown | Stato attuale del sensore di movimento |
| `door_sensor_state` | on / off / none / unknown | Stato del sensore porta |
| `occupancy_source` | door / border / motion / manual / none | Cosa ha determinato lo stato attuale |
| `border_sensors_state` | dict | Timestamp ultimo trigger per sensore di confine |
| `last_border_event` | timestamp ISO | Attraversamento confine più recente |
| `inferred_direction` | entry / exit / unknown | Direzione dell'ultimo attraversamento |
| `fully_enclosed` | true / false | Stanza con solo porta, nessun confine |

## Specifica

Di seguito la specifica originale utilizzata per implementare questa integrazione.

### Problema

Il classico Wasp in a Box funziona per stanze chiuse con porta. Ma la maggior parte delle case ha anche transizioni open-plan (archi, passaggi con tenda, corridoi). Un PIR a tenda può rilevare qualcuno che attraversa, ma produce eventi transitori anziché stato persistente — non può "sigillare" la stanza.

### Tassonomia dei sensori

| Tipo | Chiave config | Comportamento |
|---|---|---|
| Sensore porta | `box_id` | Binario aperto/chiuso. Sigilla la stanza. |
| Sensore di confine | `border_ids` | Trigger PIR transitorio al passaggio. Nessun sigillo. |
| Sensore movimento stanza | `wasp_id` | Rileva movimento all'interno della stanza. |

### Logica sensore di confine

- **Inferenza ingresso (§3.1):** PIR confine si attiva → il movimento stanza segue entro `border_correlation_window` → ingresso confermato.
- **Inferenza uscita (§3.2):** Movimento stanza assente → PIR confine si attiva → nessun movimento riprende entro `border_exit_timeout` → uscita confermata.
- **Passaggio transitorio:** Confine si attiva senza successivo movimento stanza → transitorio, nessun cambio di occupazione.

### Modalità macchina a stati

- **Porta + confini (§4.1):** Porta chiusa + movimento → occupato (classico). Porta aperta + ingresso confine → occupato. La logica porta ha priorità.
- **Solo confine (§4.2):** Correlazione confine + movimento → occupato. Movimento assente per exit timeout → non occupato.

### Casi limite (§7)

- Sensori di confine multipli con debounce entro 2s.
- Lo stesso PIR confine referenziato da due stanze viene valutato indipendentemente.
- Confini misti: `fully_enclosed: false` quando i confini sono presenti.
- Cooldown PIR: `border_correlation_window` deve superare il cooldown hardware del PIR.
- Riavvio HA: stati attuali riprodotti, cronologia confine impostata su sconosciuto fino al primo evento.

### Servizi (§8)

- `reset` — azzera occupazione, timer, cronologia confine.
- `force_entry` — imposta manualmente occupato.
- `force_exit` — imposta manualmente non occupato.

### Retrocompatibilità (§9)

Tutti i nuovi campi sono opzionali con valori predefiniti che producono comportamento identico all'integrazione originale. Le voci di configurazione migrano automaticamente da v1.1 a v1.2.

## Licenza

MIT — vedi [LICENSE](LICENSE).
