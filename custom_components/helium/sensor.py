import asyncio
import logging
from datetime import timedelta

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.components.sensor import PLATFORM_SCHEMA
from homeassistant.const import (
    ATTR_ICON,
    ATTR_NAME,
    ATTR_UNIT_OF_MEASUREMENT,
    CONF_NAME,
    CONF_URL,
)
from homeassistant.core import callback
from homeassistant.exceptions import PlatformNotReady
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.restore_state import RestoreEntity

from .client import SimpleHeliumClient
from .const import (
    ATTR_ADDRESS,
    ATTR_ATTRIBUTION,
    ATTR_BLOCK,
    ATTR_TIMESTAMP,
    ATTRIBUTION,
    CONF_TIMEOUT,
    CONF_WALLET,
    CONF_HOTSPOT,
    DEFAULT_NAME,
    DEFAULT_TIMEOUT,
    DOMAIN,
    ICON_HOTSPOT,
    ICON_WALLET
)

LOG = logging.getLogger(__name__)

DATA_UPDATED = "helium_data_updated"

SCAN_INTERVAL = timedelta(minutes=15)

USD_DIVISOR = 100000000

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        # FIXME: rather than specifying sensor: - platform: helium, this should be under helium: domain
        # FIXME: ensure WALLET or HOTSPOT is specified
        vol.Optional(CONF_WALLET): [cv.string],
        vol.Required(CONF_HOTSPOT): [cv.string],
        vol.Optional(CONF_TIMEOUT, default=DEFAULT_TIMEOUT): cv.positive_int
    }
)

async def async_setup_platform(hass, config, async_add_entities_cb, discovery_info=None):
    """Set up the Helium Hotspot sensor integration."""
    wallets = config.get(CONF_WALLET)
    hotspots = config.get(CONF_HOTSPOT)
    timeout = config.get(CONF_TIMEOUT)

    create_hotspot_sensors_for_wallet = False

    sensors = []
    client = SimpleHeliumClient(timeout=timeout)

    sensors.append( HeliumPriceSensor(hass, client, async_add_entities_cb) )

    if wallets:
        for wallet_address in wallets:
            sensors.append( HeliumWalletSensor(hass, config, wallet_address, client, async_add_entities_cb)

            if create_hotspot_sensors_for_wallet:
                data = await client.async_get_wallet_data(wallet_address)
                LOG.warning(f"Wallet {wallet_address} loaded: {data}")
                #for hotspot in hotspots:
                #hotspots.append(hotspot_address)
            else:
                LOG.warning("What?")

    for hotspot_address in hotspots:
        # create the core Helium Hotspot sensor, which is responsible for updating its associated sensors
        sensors.append( HeliumHotspotSensor(hass, config, hotspot_address, client, async_add_entities_cb) )

    async_add_entities_callback(sensors, True)

# FIXME: update price every N minutes (default 2)
class HeliumPriceSensor(Entity):
    """Helium HNT price sensor (from Oracle)"""

    def __init__(self, hass, helium_client, async_add_entities_callback):
        self.hass = hass

        self._unique_id = 'helium_oracle_hnt_price'
        self._name = 'Helium HNT Oracle Price'
        self._attrs = { ATTR_ATTRIBUTION: ATTRIBUTION }

        self._client = helium_client
        self._async_add_entities_callback = async_add_entities_callback
        self._state = None

    @property
    def name(self):
        """Return the name of the sensor."""
        return self._name

    @property
    def state(self):
        """Current Helium HNT price"""
        return self._state

    @property
    def unit_of_measurement(self):
        """HNT Oracle price is always in USD"""
        return 'USD'

    @property
    def should_poll(self):
        return True

    async def async_update(self):
        """Get the latest data from the source and updates the state."""

        # trigger an update of this sensor (and all related sensors)
        client = self._helium_client
        json = await client.async_get_oracle_price()

        if json:
            data = json['data']
            self._state = round(int(data['price']) / USD_DIVISOR, 2)
            self._attr[ATTR_TIMESTAMP] = data['timestamp']
            self._attr[ATTR_BLOCK] = int(data['block'])

    @property
    def device_state_attributes(self):
        """Return the any attributes."""
        return self._attrs


class HeliumWalletSensor(Entity):
    """Helium wallet core sensor (adds related sensors)"""

    def __init__(self, hass, config, wallet_address, helium_client, async_add_entities_callback):
        """Initialize the Helium wallet sensor."""
        self.hass = hass

        self._address = wallet_address
        self._unique_id = f"helium_wallet_{wallet_address}"

        self._attrs = {
            ATTR_ATTRIBUTION: ATTRIBUTION,
            ATTR_ADDRESS: wallet_address
        }

        self._client = helium_client
        self._async_add_entities_callback = async_add_entities_callback

        self._json = None
        self._name = f"Helium Wallet {wallet_address}"
        self._state = None        

        # FIXME: create all the dependent sensors

    @property
    def name(self):
        """Return the name of the sensor."""
        return self._name

    @property
    def state(self):
        """Return current wallet balance"""
        return self._state

    @property
    def unit_of_measurement(self):
        """HNT Oracle price is always in USD"""
        return 'USD'

    @property
    def icon(self):
        return ICON_WALLET

    @property
    def should_poll(self):
        return True

    async def async_update(self):
        """Get the latest data from the source and updates the state."""

        # trigger an update of this sensor (and all related sensors)
        client = self._helium_client

        # peel back the onion one layer to make access simpler for dependent sensors
        json = await client.async_get_wallet_data(self._address)['data']

        if not json:
            return
        self._json = json

        self._state = round(int(json['balance']) / USD_DIVISOR, 2)

        # copy useful attributes for the hotspot
        copy_attributes = [ 'block', 'dc_balance' ]
        for attr in copy_attributes:
            self._attrs[attr] = json[attr]

    @property
    def device_state_attributes(self):
        """Return the any state attributes."""
        return self._attrs

    @property
    def json(self):
        """Return the JSON structure from last hotspot update"""
        return self._json



class HeliumHotspotSensor(Entity):
    """Helium hotspot core sensor (adds related sensors)"""

    def __init__(self, hass, config, hotspot_address, helium_client, async_add_entities_callback):
        """Initialize the core Helium Hotspot sensor."""
        self.hass = hass

        self._address = hotspot_address
        self._unique_id = f"helium_hotspot_{hotspot_address}"

        self._attrs = {
            ATTR_ATTRIBUTION: ATTRIBUTION,
            ATTR_ADDRESS: hotspot_address
        }

        self._client = helium_client
        self._async_add_entities_callback = async_add_entities_callback

        # get name from the websevice
        json = await helium_client.async_get_hotspot_data(self._address)
        if json:
            self._json = json['data']
            self._name = 'Helium ' + self._json['name']
            self._state = self._json['status']['online']
        else:
            self._json = None
            self._name = hotspot_address
            self._state = None        

        # create all the dependent sensors

        # FIXME: Diff URL for reward_scale
        # https://api.helium.io/v1/hotspots/{address}}/rewards/sum?min_time=2021-06-02T08:09:39Z

    @property
    def name(self):
        """Return the name of the sensor."""
        return "Helium " + self._name

    @property
    def state(self):
        """Return the log types being tracked in Helium Hotspot."""
        return self._state

    @property
    def icon(self):
        return ICON_HOTSPOT

    @property
    def should_poll(self):
        return True

    async def async_update(self):
        """Get the latest data from the source and updates the state."""

        # trigger an update of this sensor (and all related sensors)
        client = self._helium_client

        # peel back the onion one layer to make access simpler for dependent sensors
        json = await client.async_get_hotspot_data(self._address).get('data')

        if not json:
            return
        self._json = json

        self._name = json['name']
        self._state = json['status']['online']

        # copy useful attributes for the hotspot
        copy_attributes = [ 'block', 'reward_scale', 'owner', 'last_poc_challenge',  ]
        for attr in copy_attributes:
            self._attrs[attr] = json[attr]

        # FIXME: trigger dependancies to update

    @property
    def device_state_attributes(self):
        """Return the any state attributes."""
        return self._attrs

    @property
    def json(self):
        """Return the JSON structure from last hotspot update"""
        return self._json


class DependentSensor(RestoreEntity):
    """Representation of a sensor whose state is dependent on another sensor's data."""

    def __init__(self, hass, name, sensor_type, unique_id, update_trigger,  resolver_function):
        """Initialize the sensor."""
        super().__init__()

        self.hass = hass

        self._name = name
        self._sensor_type = sensor_type
        self._unique_id = unique_id

        self._update_trigger = update_trigger

        self._state = None
        self._resolver_function = resolver_function

        self._attrs = { ATTR_ATTRIBUTION: ATTRIBUTION }

        # FIXME: listen to update trigger for updates

    @property
    def name(self):
        """Return the name of the sensor."""
        return self._name

    @property
    def unique_id(self):
        return self._unique_id

    @property
    def should_poll(self):
        return True  # FIXME: get scheduled updates working below

    @property
    def device_state_attributes(self):
        """Return the any state attributes."""
        return self._attrs


class UpdatableSensor(RestoreEntity):
    """Representation of a sensor whose state is kept up-to-date by an external data source."""

    def __init__(self, hass, unique_id, name, config, sensor_type):
        """Initialize the sensor."""
        super().__init__()

        self.hass = hass
        self._name = name
        self._config = config
        self._sensor_type = sensor_type
        self._state = None
        self._unique_id = unique_id

        self._attrs = { ATTR_ATTRIBUTION: ATTRIBUTION }

    @property
    def name(self):
        """Return the name of the sensor."""
        return self._name

    @property
    def unique_id(self):
        return self._unique_id

    @property
    def should_poll(self):
        return True  # FIXME: get scheduled updates working below

    @property
    def unit_of_measurement(self):
        """Return the unit the value is expressed in."""
        return self._config[ATTR_UNIT_OF_MEASUREMENT]

    @property
    def state(self):
        """Return the state of the device."""
        return self._state

    @property
    def device_state_attributes(self):
        """Return the any state attributes."""
        return self._attrs

    @property
    def icon(self):
        return self._config["icon"]

    def inject_state(self, state, timestamp):
        state_changed = self._state != state
        self._attrs[ATTR_TIMESTAMP] = timestamp

        if state_changed:
            self._state = state

            # FIXME: see should_poll
            # notify Home Assistant that the sensor has been updated
            # if (self.hass and self.schedule_update_ha_state):
            #    self.schedule_update_ha_state(True)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        # for this integration, restoring state really doesn't matter right now (but leaving code below in place)
        # Reason: all the sensors are dynamically created based on Helium Hotspot service call, which always returns
        # the latest state as well!
        if self._state:
            return

        # on restart, attempt to restore previous state (SEE COMMENT ABOVE WHY THIS ISN'T USEFUL CURRENTLY)
        # (see https://aarongodfrey.dev/programming/restoring-an-entity-in-home-assistant/)
        state = await self.async_get_last_state()
        if not state:
            return
        self._state = state.state
        LOG.debug(f"Restored sensor {self._name} previous state {self._state}")

        # restore attributes
        if ATTR_LOG_TIMESTAMP in state.attributes:
            self._attrs[ATTR_LOG_TIMESTAMP] = state.attributes[ATTR_LOG_TIMESTAMP]

        async_dispatcher_connect(
            self.hass, DATA_UPDATED, self._schedule_immediate_update
        )

    @callback
    def _schedule_immediate_update(self):
        self.async_schedule_update_ha_state(True)
