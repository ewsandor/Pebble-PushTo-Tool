'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

async function main() {
  const listeners = {};
  const sent = [];
  const gps = [];
  const subscriptions = [];
  const storage = {
    'astroslop.settings.v1': JSON.stringify({useWeather: 1}),
    'astroslop.lastGpsFix.v1': JSON.stringify({latitude: 51.5, longitude: -0.1, elevation: 0})
  };
  let permission = 'granted';
  const opened = [];
  let clay;
  class FakeClay {
    constructor(config, customFn, options) {
      assert.equal(options.autoHandleEvents, false);
      this.config = config;
      this.meta = {userData: {}};
      clay = this;
    }
    setSettings(settings) { this.settings = settings; }
    generateUrl() { return 'data:clay'; }
    getSettings(response, convert) {
      assert.equal(convert, false);
      return JSON.parse(response);
    }
  }
  const context = {
    console: {log() {}},
    require(id) {
      if (id === '@rebble/clay') return FakeClay;
      if (id === './config') {
        return require(path.join(__dirname, '../../src/pkjs/config.js'));
      }
      assert.equal(id, '../../resources/generated/catalog_credits.json');
      return [{title: 'Test catalog', source: 'Test source', license: 'CC0-1.0'}];
    },
    setTimeout,
    localStorage: {
      getItem(key) { return storage[key] || null; },
      setItem(key, value) { storage[key] = value; }
    },
    navigator: {geolocation: {
      getCurrentPosition(success, failure, options) { gps.push({success, failure, options}); }
    }},
    Pebble: {
      addEventListener(name, callback) { listeners[name] = callback; },
      openURL(url) { opened.push(url); },
      getActiveWatchInfo() { return {platform: 'emery'}; },
      sendAppMessage(message, success) { sent.push(message); success(); },
      permissions: {query() { return Promise.resolve(permission); }},
      subscribeToSource(options) {
        const sub = {options, unsubscribed: false, unsubscribe() { this.unsubscribed = true; }};
        subscriptions.push(sub);
        return sub;
      }
    }
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../src/pkjs/index.js'), 'utf8'),
                     context);
  listeners.ready({});
  assert.equal(JSON.parse(storage['pebble-pushto-tool.settings.v1']).useWeather, 1);
  assert.deepEqual(JSON.parse(storage['pebble-pushto-tool.lastGpsFix.v1']),
                   {latitude: 51.5, longitude: -0.1, elevation: 0});
  assert.equal(sent[0].JS_READY, 1);
  assert.equal(sent[1].CFG_USE_WEATHER, 1);
  assert.equal(sent[1].CFG_REFRACTION, 1);
  assert.equal(sent[1].CFG_FLASHLIGHT_ENABLED, 1);
  listeners.appmessage({payload: {REQ_LOCATION: 2}});
  assert.equal(gps[0].options.maximumAge, 0);
  gps[0].success({coords: {latitude: 51.5, longitude: -0.1, altitude: null, accuracy: 5}});
  let fix = sent.at(-1);
  assert.equal(fix.LOC_LAT_E6, 51500000);
  assert.equal(fix.LOC_LON_E6, -100000);
  assert.equal(fix.LOC_ALT_VALID, 0);
  assert.equal(fix.LOC_ALT_M, 0);
  listeners.appmessage({payload: {REQ_LOCATION: 1}});
  gps.at(-1).failure(undefined);
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(sent.at(-1).LOC_ERROR, 3);

  function page(values) {
    const response = {};
    Object.keys(values).forEach(key => { response[key] = {value: values[key]}; });
    listeners.webviewclosed({response: JSON.stringify(response)});
    return new Promise(resolve => setTimeout(resolve, 0));
  }
  async function open() {
    listeners.showConfiguration({});
    await new Promise(resolve => setTimeout(resolve, 0));
    return clay;
  }
  let shown = await open();
  assert.equal(opened.length, 1);
  assert.equal(shown.settings.CFG_MIN_ELEV, 0);
  assert.equal(shown.settings.CFG_SHOW_TIME, true);
  assert.equal(shown.settings.CFG_USE_WEATHER, true);
  assert.equal(shown.settings.CFG_REFRACTION, true);
  assert.equal(shown.settings.CFG_LABEL_MODE, '2');
  assert.equal(shown.settings.LATITUDE, '51.4778');
  assert.equal(shown.meta.userData.lastFix.latitude, 51.5);
  const configText = JSON.stringify(shown.config);
  const { pebble, version } = require('../../package.json');
  assert.ok(configText.includes(`<strong>${pebble.displayName} ${version}</strong>`));
  assert.match(configText, /Minor-planet elements are valid from 2026 through 2036/);
  assert.match(configText, /Sonia Keys \(MIT\)/);
  assert.match(configText, /Pebble iconography, Apache-2\.0/);
  assert.match(configText, /<strong>Test catalog<\/strong><br>Source: Test source<br>Terms: CC0-1\.0/);
  assert.match(configText, /<strong>Algorithms and code<\/strong>/);
  assert.match(configText, /<strong>Catalog sources and terms<\/strong>/);
  assert.match(configText, /personal and scientific use only/);
  assert.match(configText, /bending of light by the atmosphere/);
  assert.match(configText, /larger numbers are dimmer/);
  assert.match(configText, /Coordinate precision/);

  const buildConfig = require('../../src/pkjs/config.js');
  for (const platform of ['basalt', 'chalk', 'diorite', 'flint']) {
    const lowMemoryConfig = JSON.stringify(buildConfig({platform, catalogCredits: []}));
    assert.doesNotMatch(lowMemoryConfig, /CFG_PRECISION/);
  }
  for (const platform of ['emery', 'gabbro']) {
    assert.match(JSON.stringify(buildConfig({platform, catalogCredits: []})),
                 /CFG_PRECISION/);
  }

  let count = sent.length;
  await page({CFG_MIN_ELEV: 61, CFG_LABEL_MODE: '1', CFG_SHOW_TIME: true, LATITUDE: ''});
  assert.equal(sent.length, count + 1);
  assert.deepEqual({...sent.at(-1)}, {CFG_LABEL_MODE: 1});
  count = sent.length;
  await page({CFG_LABEL_MODE: '1'});
  assert.equal(sent.length, count);

  await page({CFG_USE_WEATHER: true});
  let weatherSubs = subscriptions.length;
  assert.ok(weatherSubs >= 1);
  const expiry = Date.now() + 1800000;
  subscriptions.at(-1).options.onData({
    validUntilMs: expiry,
    instances: [{properties: {temperature: {numericValue: {value: 68, unit: '\u00b0F'}}}}]
  });
  let weather = sent.at(-1);
  assert.equal(weather.WX_TEMP_DC, 200);
  assert.equal(weather.WX_TEMP_UNIT, 1);
  assert.equal(weather.WX_EXPIRES, Math.floor(expiry / 1000));
  listeners.appmessage({payload: {REQ_LOCATION: 1}});
  assert.equal(subscriptions.at(-1).unsubscribed, true);
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(subscriptions.length, weatherSubs + 1);
  subscriptions.at(-1).options.onData({
    instances: [{properties: {temperature: {numericValue: {value: 10, unit: '\u00b0C'}}}}]
  });
  weather = sent.at(-1);
  assert.ok(weather.WX_EXPIRES >= Math.floor(Date.now() / 1000) + 10799);
  subscriptions.at(-1).options.onError(undefined);
  assert.equal(sent.at(-1).WX_CLEAR, 1);

  await page({CFG_USE_WEATHER: false});
  permission = 'denied';
  shown = await open();
  assert.match(JSON.stringify(shown.config), /permission is off/);
  assert.match(JSON.stringify(shown.config), /standard 10\u00b0C/);
  weatherSubs = subscriptions.length;
  await page({CFG_USE_WEATHER: true});
  assert.equal(sent.at(-2).CFG_USE_WEATHER, 1);
  assert.equal(sent.at(-1).WX_CLEAR, 1);
  assert.equal(subscriptions.length, weatherSubs);
  listeners.appmessage({payload: {REQ_LOCATION: 1}});
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(subscriptions.length, weatherSubs);
  assert.equal(JSON.parse(storage['pebble-pushto-tool.settings.v1']).useWeather, 1);
  listeners.ready({});
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(sent.at(-1).WX_CLEAR, 1);
  assert.equal(subscriptions.length, weatherSubs);
  permission = 'granted';
  await page({CFG_USE_WEATHER: false});
  delete context.Pebble.permissions;
  await page({CFG_USE_WEATHER: true});
  assert.equal(sent.at(-1).CFG_USE_WEATHER, 1);
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(subscriptions.length, weatherSubs + 1);

  await page({MANUAL: true, LATITUDE: '10.5', LONGITUDE: '-20.25', ELEVATION: '100'});
  fix = sent.at(-1);
  assert.equal(fix.LOC_SOURCE, 1);
  assert.equal(fix.LOC_LAT_E6, 10500000);
  assert.equal(fix.LOC_LON_E6, -20250000);
  assert.equal(fix.LOC_ALT_M, 100);
  const pendingGps = gps.at(-1);
  pendingGps.success({coords: {latitude: 0, longitude: 0, altitude: 0, accuracy: 5}});
  assert.equal(sent.at(-1).LOC_SOURCE, 1);
  await page({ELEVATION: '200'});
  assert.equal(sent.at(-1).LOC_ALT_M, 200);
  console.log('PebbleKit JS companion tests passed');
}

main().catch(error => { console.error(error); process.exitCode = 1; });
