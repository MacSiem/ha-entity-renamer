"""Public profile material + real native producer inputs for owner fixture.

Shared actual producer/profile input helper; never a vendor substitute.
The native graph test composes it with ordinary official vendor setup.
This helper alone does not execute validation or prove a row/family complete.
"""
from pathlib import Path
import hashlib

import test_native_input_baseline as fixtures

# Follow the existing native fixture's dependency guard. Absence of HA is a
# native skip, never a substitute producer and never a discovery import error.
if fixtures.HAS_HA:
    from homeassistant.components.light import LightEntity, ColorMode
    from homeassistant.components.select import SelectEntity

PUBLIC_BLOBS = {'lifx/LHB30E27UC10P/infrared_100/color_temp.csv': 'ed7682b7b43c9c79121d8ac3fb628868231a164636f9a941bc90255c2ded1a04', 'lifx/LHB30E27UC10P/infrared_100/color_temp.csv.gz': 'b659f7e61967d5f3e6144e18d2dc422d8d8f37d7e9aa6d9ac4974b7aadd98f74', 'lifx/LHB30E27UC10P/infrared_100/hs.csv': '0eb144f72ee48a9985ed29b1e1c04eb32bb55779e823e7d27c0c2c9601fdd86d', 'lifx/LHB30E27UC10P/infrared_100/hs.csv.gz': '9786c22d7c1afe52009bd94e739ec5acdddbe30e0911976da2e0a64eaf4ef677', 'lifx/LHB30E27UC10P/infrared_100/model.json': '2603fefb9084f673857d4a131f903534f40b3693b61b1675bc8b82fc26e5637c', 'lifx/LHB30E27UC10P/infrared_25/color_temp.csv': '8ed4d01c0cfbc68fed56ca1326db9feb205e904f71749537e2ee8061ce2baf93', 'lifx/LHB30E27UC10P/infrared_25/color_temp.csv.gz': 'a4459f86e7d3afb5ce87f64c73fe44106d2e887e836515ecf84ffbd9b26ea2d3', 'lifx/LHB30E27UC10P/infrared_25/hs.csv': '52721f0f0e701e86ae1fb76fb519f9c641e92a8b2ea4957516927e60e38839a8', 'lifx/LHB30E27UC10P/infrared_25/hs.csv.gz': '82b0e8b5c2767b6f6b7703794dbb2ae72d28accdc554cd3ea80c07e291c0bec0', 'lifx/LHB30E27UC10P/infrared_25/model.json': 'ef918821da714487cac4fa23c8a331aed2cbe891d78332c47d18cba00ea885fa', 'lifx/LHB30E27UC10P/infrared_50/color_temp.csv': 'f962234242791339b426fce5405041f653cb33a81a6806066efdecf975c6da31', 'lifx/LHB30E27UC10P/infrared_50/color_temp.csv.gz': '43c8279b1844151808977ef3be46b4d07576511a54f4e63cf357ef658d30bc5b', 'lifx/LHB30E27UC10P/infrared_50/hs.csv': '58d6287c24d9a61bdc3548fa492f23359f8f995fe75070b7957b90230dbb477d', 'lifx/LHB30E27UC10P/infrared_50/hs.csv.gz': '381f6eb9b3efe774d395ddf29526832ed0bbcee57f17920b3b63b8b6cb0781c8', 'lifx/LHB30E27UC10P/infrared_50/model.json': '3251b80caa14224690db45234294f2a7f48dadfa036f7141c8e8009e1546284d', 'lifx/LHB30E27UC10P/infrared_off/color_temp.csv': '296192042c1d1eb1716d676be0fbfc96cf6b253cfe8ec1387731241d889cb70a', 'lifx/LHB30E27UC10P/infrared_off/color_temp.csv.gz': '729cf6a2e06b7799d21416801d1a62770180ed81ca76e6dea6b9d570eff09c54', 'lifx/LHB30E27UC10P/infrared_off/hs.csv': 'f766277565a87c7ec4c1e7557f8cfa3996fb35c3aba609c64e4bf10232655ecb', 'lifx/LHB30E27UC10P/infrared_off/hs.csv.gz': 'e85808d2872b36d9078789a662ac9375bbcab46968a3eb23644847220162c4c7', 'lifx/LHB30E27UC10P/infrared_off/model.json': 'dbbc9466672ebb9f6f2284b9e2a56f599a375687643b9f3d471096254917aff6', 'lifx/LHB30E27UC10P/model.json': '3d3c335eea12243ec57f5882beb84a5654eb314e48154d8367e565c293b597a0', 'signify/LWA001/brightness.csv': '3e230eaad8da7b4ddf1204ef4817a00bcbba80de8e7116b22a98030f8639a826', 'signify/LWA001/brightness.csv.gz': '2d21c56e251ee8b23c7a6a26610fd615f02936b13182563df9db51ef6b250fdd', 'signify/LWA001/model.json': 'b1e738a09b525db6d331a9fdb44e289eb31d73e270c82db097f6bc8b266baafb'}


def stage_public_profiles(fixture_root, supplied_public_inputs):
    """Copy actual public measurement bytes BEFORE official vendor setup.

    A synthetic fixture's configuration input, never a native cache write.
    Refuse an existing profile destination; do not adopt or replace local data.
    """
    fixture_root, supplied = Path(fixture_root), Path(supplied_public_inputs)
    target = fixture_root / 'powercalc' / 'profiles'
    if target.exists():
        raise ValueError('fixture_profile_destination_exists')
    bundle = {}
    for relative, expected in PUBLIC_BLOBS.items():
        raw = (supplied / 'profile_library' / relative).read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError('official_public_profile_changed')
        bundle[relative] = raw
    for relative, raw in bundle.items():
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(raw)
    return target


ProfileLight = InfraredSelector = None
if fixtures.HAS_HA:
    class ProfileLight(LightEntity):
        """A real LightEntity producer; attributes are normal test input only."""
        _attr_should_poll = False
        _attr_is_on = True

        def __init__(self, name, *, dynamic=False):
            self._attr_name = name
            self._attr_unique_id = name
            self._attr_brightness = 1
            self._attr_supported_color_modes = ({ColorMode.COLOR_TEMP, ColorMode.HS}
                                                 if dynamic else {ColorMode.BRIGHTNESS})
            self._attr_color_mode = ColorMode.COLOR_TEMP if dynamic else ColorMode.BRIGHTNESS
            self._attr_color_temp_kelvin = 9000 if dynamic else None
            self._attr_min_color_temp_kelvin = 1500
            self._attr_max_color_temp_kelvin = 10000
            self._attr_hs_color = (0.0, 0.0)

        async def async_turn_on(self, **kwargs):
            self._attr_is_on = True
            if 'brightness' in kwargs:
                self._attr_brightness = kwargs['brightness']
            if 'color_temp_kelvin' in kwargs:
                self._attr_color_mode = ColorMode.COLOR_TEMP
                self._attr_color_temp_kelvin = kwargs['color_temp_kelvin']
            if 'hs_color' in kwargs:
                self._attr_color_mode = ColorMode.HS
                self._attr_hs_color = kwargs['hs_color']
            self.async_write_ha_state()

        async def async_turn_off(self, **kwargs):
            self._attr_is_on = False
            self.async_write_ha_state()


    class InfraredSelector(SelectEntity):
        """Separate actual SelectEntity consumed by the official entity_state matcher."""
        _attr_should_poll = False
        _attr_options = ['Disabled', '25%', '50%', '100%']
        _attr_current_option = 'Disabled'

        def __init__(self, source_object_id):
            self._attr_name = source_object_id + ' infrared brightness'
            self._attr_unique_id = source_object_id + '_infrared_brightness'
            self.entity_id = 'select.' + source_object_id + '_infrared_brightness'

        async def async_select_option(self, option):
            if option not in self._attr_options:
                raise ValueError('invalid_synthetic_input_option')
            self._attr_current_option = option
            self.async_write_ha_state()
