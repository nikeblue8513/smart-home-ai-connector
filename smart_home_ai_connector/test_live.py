import asyncio
import unittest
from unittest.mock import AsyncMock
from connector import HomeAssistantClient
from connector_live import live_endpoint, wait_for_wake
from connector_protocol import service_data

class LiveTests(unittest.IsolatedAsyncioTestCase):
    async def test_notification_wakes_without_poll_wait(self):
        wake = asyncio.Event()
        task = asyncio.create_task(wait_for_wake(wake, 15))
        wake.set()
        await asyncio.wait_for(task, .1)

    async def test_missing_notification_falls_back(self):
        await asyncio.wait_for(wait_for_wake(asyncio.Event(), .01), .1)

    async def test_notifications_do_not_bypass_failure_backoff(self):
        wake = asyncio.Event(); wake.set()
        task = asyncio.create_task(wait_for_wake(wake, .05, 1))
        await asyncio.sleep(.01)
        self.assertFalse(task.done())
        await task

    async def test_registry_is_cached_but_states_stay_live(self):
        ha = HomeAssistantClient(None, 'http://supervisor/core/api', 'internal')
        ha._registry_snapshot = AsyncMock(return_value={'areas': [], 'devices': [], 'entities': []})
        ha._rest = AsyncMock(side_effect=[{'location_name': 'Home'}, [{'entity_id': 'light.lamp', 'state': 'off', 'attributes': {}}], [{'entity_id': 'light.lamp', 'state': 'on', 'attributes': {}}]])
        first = await ha.snapshot('a' * 32); second = await ha.snapshot('a' * 32)
        self.assertEqual(first['states'][0]['state'], 'off')
        self.assertEqual(second['states'][0]['state'], 'on')
        self.assertEqual(ha._registry_snapshot.await_count, 1)
        self.assertIn('light.lamp', ha.exported_entities)
        self.assertTrue(second['capabilities']['live_delivery'])

    def test_delivery_cannot_redirect_to_other_origin(self):
        info = {'url': 'https://project.example', 'apikey': 'public-key-' * 3, 'topic': 'homecore-wake-' + 'a' * 64}
        self.assertEqual(live_endpoint(info, 'https://project.example/functions/v1/homecore-bridge')[0], 'wss://project.example/realtime/v1/websocket')
        for url in ('https://evil.example', 'http://project.example', 'https://project.example/private', 'https://user@project.example'):
            with self.assertRaises(ValueError): live_endpoint({**info, 'url': url}, 'https://project.example/bridge')

    def test_white_color_saturation_and_native_transitions(self):
        self.assertEqual(service_data('light','turn_on','light.lamp',{'hue':120,'saturation':45})['hs_color'], [120,45])
        self.assertEqual(service_data('light','turn_on','light.lamp',{'colorTempKelvin':3000})['color_temp_kelvin'], 3000)
        self.assertEqual(service_data('light','turn_off','light.lamp',{}), {'entity_id':'light.lamp'})
        self.assertEqual(service_data('light','turn_on','light.lamp',{}), {'entity_id':'light.lamp'})
        self.assertNotIn('transition', service_data('light','turn_on','light.lamp',{'brightnessPct':42}))
        for parameters in ({'saturation':10},{'hue':10,'colorTempKelvin':3000},{'hue':10,'saturation':101},{'colorTempKelvin':999},{'colorTempKelvin':True}):
            with self.assertRaises(ValueError): service_data('light','turn_on','light.lamp',parameters)
