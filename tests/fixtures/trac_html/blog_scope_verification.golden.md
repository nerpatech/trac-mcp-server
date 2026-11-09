# The topic said 46.8%. That's not proof of anything.

[#17](https://trac.example.org/bcs/ticket/17) | b-node 4.0.0 (`main/sensor_bme280.c`) | 2026-08-12 \
Angle: a plausible number on MQTT and a verified wire protocol are different categories of evidence, and only the scope below the stack could settle the second.

- Companions, same session: [compensation-formula-precedence](https://trac.example.org/bcs/wiki/b-node/blog/compensation-formula-precedence) (the bug) and [hardware-in-the-loop](https://trac.example.org/bcs/wiki/b-node/blog/hardware-in-the-loop) (what only a human can report; this page is what only an instrument below the stack can prove).
- By the software view node-b's BME280 already looked right: `sensors/node-b/weather/#` showed temperature \~24 C, pressure \~832 hPa (real for this elevation, not 1013) and humidity 46.8%.
- A breath test moved temperature and humidity together and let them decay over the next two samples, the same rise-then-decay signature the DHT22 bench used as its strongest evidence.
- Why that is not proof: between the I2C transaction and the MQTT number sit the compensation math, deci-unit scaling, the delta/resync encoding (`tlm_delta.c`), LoRa TLV framing, the gateway forward and the MQTT client; a bug in any layer can leave a believable number.
- The same session had just found one that gave an implausible number (0.0%) and was caught only because it was implausible; a plausible-but-wrong one, or the right-shaped garbage from the wrong device, would have passed the same MQTT observation.
- Instrument: Rigol MSO2302A on SDA/SCL (D2/D1, shared with the OLED), I2C decode triggered on address `0x76`, one forced-mode read captured.
- Decoded: `Write 76: F4 25` (ctrl_meas, forced, osrs_t x1, osrs_p x1); `Write 76: F3`; `Read 76: 04` (measuring bit clear); `Write 76: F7`; `Read 76: 71 B9 00 80 FE 00 76 4A` (press, temp, hum in one transaction).
- Matches `main/sensor_bme280.c` line for line: `BME280_CTRL_MEAS_FORCED` is `0x25`, the status poll is `0xF3`, the burst read starts at `0xF7` for 8 bytes via `bme280ReadRegs(h, BME280_REG_DATA, data, sizeof(data))`.
- It cannot be fooled by the compensation math, delta encoder, LoRa framer or MQTT client, since the capture never passes through them.
- Wrong turn, first screenshot: seven unrelated digital channels (D6/D8/D10-D14, leftovers from an unconnected HD44780 setup), a noisy analog channel and four stale measurement readouts (`Freq12`, `+Width5`, `Vpp`) crowding out SDA/SCL.
- Three tool calls cleared it (`configure_digital_channels(enabled: false)` on seven channels, `configure_analog_channel(enabled: false)` on CH1, `:MEASure:CLEar ALL`) and mattered more than any trigger choice.
- An address-match trigger armed `single` has to actually be armed: status goes `WAIT`, fires, `TD`/`STOP`; reading it afterwards, not trusting the call's return, is how a fresh capture was told from still-waiting and from a stale frame.
- Wrong timebase, first: 20 us/div shows 280 us and the exchange spans \~600 us, so it caught only the burst read; 100 us/div caught all of it but made the byte labels illegible.
- Answer was four crops at 20 us/div at four horizontal offsets plus one wide overview; no single timebase is both wide enough and readable.
- What it would have caught: `I2cBus_get()` (`main/i2c_bus.c`) matching a bus entry on the wrong criterion and handing the BME280 driver the OLED's device handle; both parts ACK, a number might still arrive, and the decode would show `0x3C` where `0x76` belongs.
- Scope of the claim: most driver bugs in this project were caught without a scope (breath tests, checksum failures, log lines); a believable MQTT number is enough to stop debugging, not enough to close a ticket claiming the wire protocol is correct.

## Attachments

- `bme280-exchange-part1.png` to `bme280-exchange-part4.png` - the four crops, one continuous exchange

[![Part 1: ctrl_meas write (0xF4=0x25), status-register pointer write (0xF3)](https://trac.example.org/bcs/raw-attachment/wiki/b-node/blog/scope-verification-vs-plausible-mqtt/bme280-exchange-part1.png "Part 1: ctrl_meas write (0xF4=0x25), status-register pointer write (0xF3)")](https://trac.example.org/bcs/attachment/wiki/b-node/blog/scope-verification-vs-plausible-mqtt/bme280-exchange-part1.png)
[![Part 2: status read result (0x04, measuring bit clear)](https://trac.example.org/bcs/raw-attachment/wiki/b-node/blog/scope-verification-vs-plausible-mqtt/bme280-exchange-part2.png "Part 2: status read result (0x04, measuring bit clear)")](https://trac.example.org/bcs/attachment/wiki/b-node/blog/scope-verification-vs-plausible-mqtt/bme280-exchange-part2.png)
[![Part 3: burst-read pointer write (0xF7) plus first bytes of the read](https://trac.example.org/bcs/raw-attachment/wiki/b-node/blog/scope-verification-vs-plausible-mqtt/bme280-exchange-part3.png "Part 3: burst-read pointer write (0xF7) plus first bytes of the read")](https://trac.example.org/bcs/attachment/wiki/b-node/blog/scope-verification-vs-plausible-mqtt/bme280-exchange-part3.png)
[![Part 4: the complete 8-byte burst read](https://trac.example.org/bcs/raw-attachment/wiki/b-node/blog/scope-verification-vs-plausible-mqtt/bme280-exchange-part4.png "Part 4: the complete 8-byte burst read")](https://trac.example.org/bcs/attachment/wiki/b-node/blog/scope-verification-vs-plausible-mqtt/bme280-exchange-part4.png)
