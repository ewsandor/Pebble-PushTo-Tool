# PushTo Tool

PushTo Tool is a Pebble watchapp for visual astronomy. It shows the
live altitude and azimuth of your target so you can aim a Dobsonian or other
manual alt-az telescope with a setting circle and an angle gauge. No motors or
encoders are required. On the watch, look for **PushTo Tool** beside the
telescope icon.

**Status:** Initial implementation; hardware validation remains outstanding.
See [limitations](#limitations-and-open-issues) before using it.

## Features

- Live azimuth and elevation, displayed in degrees to 0.1° by default.
  Azimuth is measured clockwise from true north. Display refresh and optional
  0.01° precision are configurable.
- On-watch catalogs for the Sun, Moon, planets and selected minor planets; the
  100 brightest stars; 110 Messier objects; the 110 RASC Finest NGC objects;
  and the centers of the 88 IAU constellations. M102 is represented by NGC 5866.
- Phone GPS at startup and hourly, with manual coordinates as an alternative
  and a persisted last fix. Astronomy calculations run on the watch without
  phone round-trips.
- Configurable object name, local time, next rise or set time, magnitude and
  optional local weather temperature, subject to screen space.
- High-contrast display with an invert option, a configurable red backlight on
  supported watches, and elevation and magnitude filters.
- A timed flashlight, warning icons and a selection banner; credits remain in
  the phone settings.

The app targets Pebble Time 2 (**emery**), Pebble 2 Duo (**flint**) and
Pebble Round 2 (**gabbro**), as well as Pebble Time (**basalt**), Time Round
(**chalk**) and Pebble 2 (**diorite**). Aplite is not supported. Smaller and
round displays may omit secondary fields.

## Controls

| Action | Control |
|---|---|
| Previous / next object | Up / Down |
| Next catalog | Select |
| Scroll objects on the object banner | Hold Up / Down |
| Previous catalog on the object banner | Hold Select |
| Dismiss the object banner | Back |
| Refresh GPS and weather subscription on the main screen | Hold Down |
| Toggle timed flashlight on the main screen | Hold Select |

Holding Up or Down moves one object at a time and stops when released. Solar
System positions can take a moment on older watches; the banner then shows
`...` until the next object is ready.

Open the app's settings from its gear icon in the Pebble mobile app.
The settings page uses [Clay](https://github.com/pebble-dev/clay), the SDK's
recommended configuration framework; changes apply when you tap **Save**. The
elevation filter is enabled at 0° by default; disable it to browse objects below the horizon. On watches with
a white backlight, the flashlight is disabled by default. The first visit to a
catalog selects its first matching object; later visits restore the last
selection even if it no longer passes the filters.

## Interface mockups

These design renderings illustrate the main screen and temporary object banner.
They use the same scale; live values and text vary with the selected object.

| | Pebble Time 2 (emery) | Pebble Round 2 (gabbro) |
|---|---|---|
| Main screen | <img src="docs/images/readme/emery_main.png" alt="Emery main screen showing M31 azimuth and elevation" width="200"> | <img src="docs/images/readme/gabbro_main.png" alt="Gabbro main screen showing M31 azimuth and elevation" width="260"> |
| Object banner | <img src="docs/images/readme/emery_banner.png" alt="Emery object banner for M31 Andromeda Galaxy" width="200"> | <img src="docs/images/readme/gabbro_banner.png" alt="Gabbro object banner for M31 Andromeda Galaxy" width="260"> |

More platform and state mockups are in the [design document](docs/DESIGN.md#911-emulator-renderings).

Native-resolution app store screenshots are available as workflow artifacts;
see [release screenshots](#release-screenshots).

## Theory of operation

The watchapp uses the watch's system clock and a location from the Pebble
mobile app (or manual coordinates). For the selected object, it calculates an
apparent celestial position and converts it to local azimuth and elevation
using sidereal time and the observer's latitude and longitude. Solar-system
positions use planetary, lunar or orbital models; fixed objects use catalog
coordinates corrected for proper motion, precession, nutation and aberration.
Topocentric parallax is included. Elevation includes atmospheric refraction by
default, using observer elevation and optionally a weather temperature. The
display also computes the next rise or set event when enabled.

The algorithms are based in part on Jean Meeus, *Astronomical Algorithms*,
2nd edition. See the [astronomy design](docs/DESIGN.md#7-astronomy-algorithms)
for models, assumptions and accuracy targets.

**Solar safety:** Never look at the Sun through a telescope or finder without a
properly installed, purpose-made solar filter.

## Data sources

- [NASA/JPL Horizons](https://ssd.jpl.nasa.gov/horizons/) and the
  [Small-Body Database](https://ssd.jpl.nasa.gov/tools/sbdb_lookup.html):
  minor-planet orbital elements and magnitude data.
- IMCCE/Paris Observatory VSOP87 planetary theory, distributed through CDS
  VizieR; lunar and Pluto models as described by Meeus.
- Yale [Bright Star Catalogue](https://heasarc.gsfc.nasa.gov/W3Browse/star-catalog/bsc5p.html)
  via NASA HEASARC, with names from the
  [IAU Catalog of Star Names](https://www.iau.org/public/themes/naming_stars/).
- NASA HEASARC [Messier catalog](https://heasarc.gsfc.nasa.gov/W3Browse/all/messier.html)
  and NGC 2000.0; IAU constellation boundaries digitized in CDS VizieR VI/49.
- The user's weather provider, via the Pebble mobile app's weather plugin,
  when local weather is enabled.

Source acknowledgements are in [CREDITS.md](CREDITS.md) and the
[design document](docs/DESIGN.md#13-dependencies-data-sources-and-attribution).

## Building and testing

Install the [Pebble SDK](https://developer.repebble.com/sdk/) (4.44), Python 3,
Node.js with npm, and a host C compiler and `make`. The checked-in catalog and
ephemeris inputs are packed automatically. `pebble build` installs the Clay
package from npm on first build; it does not download catalog data. From the
repository root, build the app:

```sh
pebble build
```

This builds the **release** flavor by default (errors remain logged). To build the full-logging debug
flavor, use:

```sh
pebble build -- --mode=debug
```

Both flavors write `build/Pebble-PushTo-Tool.pbw`. The debug flavor has a
separate app UUID and display name, so it can be installed alongside the store version.
Rebuild the release flavor before publishing; CI uploads only that package.

For a release, update the version in the manifest and lockfile together, without
creating a Git tag:

```sh
npm version 0.1.1 --no-git-tag-version
```

The phone settings About section uses the version from `package.json`.
CI uses Ubuntu 24.04, Pebble Tool 5.2.0, and SDK 4.44 to reduce toolchain drift.

PRs, merge queues, pushes to `main`, release tags, and manual workflow runs build
both release and debug modes and run the sanity suite for both modes on every
supported platform. The aggregate **`sanity`**
job fails if any build, host test, or sanity matrix entry fails. Workflow files
cannot block merges on their own, so make it a required check once (repository
admin):

1. Open **Settings → Rules → Rulesets** and edit the ruleset targeting `main`.
2. Enable **Require status checks to pass** and add the check **`sanity`** with
   source **GitHub Actions**. Leave the per-platform `sanity-emulator` entries
   unrequired; `sanity` already covers them.
3. Save. Confirm with
   `curl -s https://api.github.com/repos/ewsandor/Pebble-PushTo-Tool/rules/branches/main`,
   which should list a `required_status_checks` rule containing `sanity`.

Push a matching `vX.Y.Z` tag to release. Once the build succeeds, the workflow
creates a GitHub Release with generated notes and the release PBW, without
waiting for tests. The release is therefore available before it has passed the
release gate. Alongside release/debug sanity checks, the comprehensive suite
tests only that release PBW in a separate
job for each supported platform and test category (`pointing`, `clock`,
`navigation`, `weather`, `display`, and `filters`). Each category/watch pair can
be rerun independently and uploads its own uniquely named report artifact.
The release gate requires every comprehensive pair and release/debug sanity
to succeed. Only after the host, sanity, and comprehensive checks pass does
`store-screenshots` capture native-resolution PNGs from the same release PBW
on every platform. The workflow then attempts to publish the tested PBW to the
Pebble App Store, unless that version already exists. Configure the GitHub
`Pebble Appstore` environment to allow version tags, set its `APPSTORE_APP_ID`
variable to the existing listing ID, and authorize the repository and workflow
for App Store GitHub Actions publishing. Environment approval rules apply.

If authentication or upload fails, the workflow reports a warning and creates
or updates one issue per version, assigned to @ewsandor for manual publication.
The issue links to the matching GitHub Release and quotes its release notes for
copying into the Pebble App Store. It also links to the workflow run, release PBW,
comprehensive reports and screenshots. The PBW, reports and screenshots are
Actions artifacts, not attachments to the issue or release,
and expire under GitHub's artifact retention policy. No issue is created when
build, test, or screenshot gates fail or when publishing is skipped or succeeds.

Closing the exact **“Publish PushTo Tool vX.Y.Z”** issue as **Done** by
@ewsandor records a successful **Pebble Appstore** deployment in GitHub. The
deployment links back to the ticket, preserving the release version and closing
actor for traceability. Closing as **Not Planned** does not create a deployment.

Set the listing's **App Name** to **PushTo Tool** in the
[Pebble Developer Dashboard](https://developer.repebble.com/dashboard).
Store installs use the listing name in the watch launcher; sideloads use
`pebble.displayName` in `package.json`. Keep both set to **PushTo Tool**.

### Release screenshots

After a successful tag run, open it under **Actions**, scroll to **Artifacts**,
and download the `store-screenshots-<platform>` ZIPs. Screenshots are not
uploaded to the App Store automatically.

The captures use 2026-01-01 12:00 AM UTC, 51.4779° N / 0° E, 45 m and
51°F weather. Each model gets Jupiter on the main screen, Sirius on the banner,
M31 on the inverted main screen, and M42 on the inverted banner. Emery also
gets Betelgeuse with simulated red backlight. Main screens show weather where the layout
has room; banners do not display weather. Watch screenshots contain framebuffer
pixels, not the physical backlight, so the Emery red image is explicitly
colorized to illustrate the red illumination, not a physical-light measurement.
Positions may differ slightly as the emulator clock advances during capture.
To reproduce a platform locally, build the release PBW and run:

```sh
pebble emu-set-location --emulator emery --vnc 51.4779 0
python3 test/emulator/store_screenshots.py --platform emery \
  --pbw build/Pebble-PushTo-Tool.pbw --output build/store-screenshots
```

The script uses the managed VNC emulator and its persistent flash; avoid running
it alongside another user's emulator. Its logs are under the output's `logs/`
directory. The Python dependencies are the same as the emulator test suite.

### Icon assets

The Dobsonian icon assets for [branding issue #11](https://github.com/ewsandor/Pebble-PushTo-Tool/issues/11)
are in `assets/icons/`: color and transparent black-and-white PNGs, plus SVG and
PDC sources, in 25x25, 50x50 and 80x80 sizes. The menu icon
(`resources/icons/app_icon.png`) uses the transparent 25x25 black-and-white PNG.
Only the 80x80 artwork includes setting-circle ticks; the smaller sizes omit them.
The 144x144 color PNG is rendered from the 80x80 artwork, including its ticks.
Transparent black-and-white PNGs for all three sizes are named
`dobsonian_<size>px_bw.png`; they use the same Pebble-rendered pixels as the
color icons, with the red background made transparent.
Use [`pdc_tool`](https://github.com/HBehrens/pdc_tool), linked by the Pebble
iconography style guide, to regenerate them without antialiasing:

```sh
pdc_tool assets/icons/dobsonian_80px.svg pdc assets/icons/dobsonian_80px.pdc
pdc_tool assets/icons/dobsonian_80px.pdc png assets/icons/dobsonian_80px.png \
  --no-antialiasing --offset 0,0 --crop 0,0,80,80 --background-color dark-candy-apple-red
```

For 50x50 or 25x25, substitute `50` or `25` for `80`. To regenerate the
transparent PNGs, replace the exact dark candy apple red background pixels in
each color PNG with transparent pixels, preserving the black and white pixels.
Copy the transparent 25x25 PNG to `resources/icons/app_icon.png` for the menu.

### Install on a watch

Pair your watch with the Pebble mobile app and sign in to your Pebble account.
On the **Devices** tab, open the watch's three-dot menu and enable
**Dev Connection**. From the repository root, sign in with the same account
and install the package:

```sh
pebble login  # needed once
pebble install --cloudpebble build/Pebble-PushTo-Tool.pbw
```

Allow the app access to your phone's location when prompted.

### Run in an emulator

```sh
pebble install --emulator emery
```

Use `--vnc` for **all** emulator commands in a headless environment, for
example:

```sh
pebble install --emulator emery --vnc
pebble screenshot --emulator emery --vnc --scale 6 --no-open screenshot.png
pebble emu-button --emulator emery --vnc click select
```

### Run the tests

Run the host tests (Node.js is needed for the companion test):

```sh
make -C test/astro
python3 -m unittest discover -s test/tools -v
node test/pkjs/companion.test.js
```

### Emulator acceptance suites

The same Python runner is used locally and in GitHub Actions; no AI, OCR,
browser automation, or downloaded reference data is involved. It reads
`package.json`'s `targetPlatforms` (currently all six non-aplite platforms).
Install SDK 4.44 and the build prerequisites above, plus SDL2, GLib, Pixman,
zlib and libsndio. On Ubuntu, the system dependencies are:

```sh
sudo apt-get install libsdl2-2.0-0 libglib2.0-0 libpixman-1-0 zlib1g libsndio7.0
python3 -m pip install -r test/emulator/requirements.txt
```

Use a Python virtual environment if your system disallows global pip installs.
Run from the repository root:

```sh
# Sanity: release and debug, every platform (PR tier)
python3 test/emulator/run.py --tier sanity

# Comprehensive: release, every platform (release tier)
python3 test/emulator/run.py --tier comprehensive

# Triage a comprehensive failure in debug on one platform
python3 test/emulator/run.py --tier comprehensive --mode debug --platform flint

# Reuse a built release PBW, as CI does (no rebuild)
python3 test/emulator/run.py --tier sanity --mode release \
  --platform chalk --pbw build/Pebble-PushTo-Tool.pbw --output build/emulator-tests

# Focused runner checks (after building the host reference exporters)
make -C test/astro references
python3 -m unittest discover -s test/emulator -v

# Discover and run just the cases relevant to a change
python3 test/emulator/run.py --tier comprehensive --list-cases
python3 test/emulator/run.py --tier sanity --mode debug --platform emery \
  --case ui-banner --case 'horizons-00?'
python3 test/emulator/run.py --tier comprehensive --mode release --platform chalk \
  --group navigation --group filters
```

On Linux, `test/emulator/parallel.py` can run platform/mode combinations in
parallel without Docker or downloading an image. It requires `unshare` (from
util-linux), `bwrap` (Bubblewrap), `ip` (iproute2), and permission to create
unprivileged user and network namespaces. The usual Pebble SDK and test
dependencies must already be installed.

```sh
# PR emulator tier, at most three isolated emulators at a time
python3 test/emulator/parallel.py --tier sanity --jobs 3

# Focused release check, reusing an existing PBW
python3 test/emulator/parallel.py --tier comprehensive --mode release \
  --platform chalk --platform emery --pbw build/Pebble-PushTo-Tool.pbw \
  --group navigation --jobs 2
```

The parallel launcher supports the same tier, mode, platform, case, group and
single-mode PBW selections as the serial runner. It builds each needed mode
once in a temporary workspace unless `--pbw` is supplied. Each worker has its
own workspace, SDK flash, temporary files, process namespace and offline
network; its simulated phone location starts at 40° N, 105° W. The installed SDK
is mounted read-only. Workers do not use or change the user's running emulator.
The default is two workers; increase `--jobs` according to available CPU, RAM
and disk space. Namespace restrictions may prevent this option on some systems;
the serial runner remains available.

Each invocation creates a new directory under `build/emulator-parallel`
(or `--output`) and prints its path. Its `summary.json` aggregates worker
results; `workers/<mode>/<platform>/` contains the normal runner reports and
`worker.log`, including startup failures. Temporary workspaces and emulator
state are discarded after the run. As with the serial suite, quarantined cases
are reported as skipped, not passed.

With SDK 4.44, a fresh Diorite emulator has intermittently reset during app
installation. The worker reports this as a failure rather than retrying it;
rerun the affected platform to distinguish a transient installation failure
from a repeatable test failure.

`--case` accepts a case name or a quoted shell-style glob. `--group` selects
`pointing`, `clock`, `navigation`, `weather`, `display`, or `filters`. Both
options can be repeated or combined; they select the union of matching cases in
the chosen tier. Unknown case patterns and groups with no cases in that tier
fail rather than silently passing. A focused run is not a substitute for the
unfiltered PR or release gate.

The runner builds once per mode, copies each PBW before another mode overwrites
the build output, and reuses each platform's emulator. All emulator commands
include `--vnc`; the log connection is closed when each run finishes, including
on failure. Tests change emulator time, location, settings and object selection;
do not use an emulator concurrently for interactive work. The serial runner
uses the Pebble CLI's managed VNC emulator, which uses one display and can stop
another VNC emulator when switching platforms. Use the isolated parallel
launcher above for concurrent local runs. GitHub Actions runs the sanity
platform/mode matrix and the comprehensive platform/category matrix in parallel
on separate runners. For IPv4-only
containers, use the temporary `PYTHONPATH` workaround in [AGENTS.md](AGENTS.md).

**Sanity** covers Sun, Moon, planets, a minor planet, a star and a DSO, both
hemispheres, button navigation, normal/precise coordinate fonts and both themes.
The precise-coordinate case is reported as skipped on 64 KB platforms, which
always display 0.1°; it remains active on Emery and Gabbro.
It also tests banner navigation and dismissal, held-button repeat and release,
flashlight toggle, location refresh, and weather temperature/refraction.
It also checks rectangular main-screen spacing and one-pixel separators in both
display themes and available precisions, including a status-icon case.
Comprehensive adds clock/event/magnitude and label settings, location-error
recovery, elevation filtering, the no-matching-objects notice, and Back from the
main screen.
The phone settings page and catalog corruption/evaluation errors are not covered
by these emulator interactions. Interaction checks read rendered-frame state,
not OCR: screenshot ink/geometry checks apply to coordinate rows and rectangular
main-screen spacing, not banner or optional-field text. Chalk intentionally has
no event/magnitude footer or temperature text, although their screen state is
still checked.
Both tiers also check that a running app survives a backward UTC clock change.
That regression is explicitly quarantined for release/Basalt, Chalk and Diorite,
and debug/Basalt and Chalk, due to a
[known clock-change failure](test/emulator/clock-change-issue.md). The linked issue
draft must be filed, and the test must be re-enabled before that bug can close.
Their reference and screenshot cases remain active: fixture time is set while
the app is stopped, then the app is relaunched without retrying failed checks.
The runner retries a screenshot once if the Pebble CLI reports a screenshot
protocol timeout; both attempts are recorded in the command log. Other failures
and failed image checks are not retried. Retries are also printed to the console,
since successful runs discard their command logs.
The SDK's public app-run-state protocol confirms stopping through its
noninteractive Python REPL; a renderer capture confirms startup. Fixture changes
do not repeatedly reinstall the PBW.
Cases sharing an already-applied fixture epoch let the clock advance naturally
instead of stopping the app just to set the same epoch again.
**Comprehensive** reuses all 66 Horizons pointing vectors and all 12 ERFA
stellar vectors from the host tests. It adds host/watch CPU-parity comparisons
for every solar-system catalog entry and M1/M31/M42/M102 at Greenwich, Chile
and Tromso. These added host-derived comparisons are not independent accuracy
references; the Horizons/ERFA vectors supply those.

Opt-in `TEST_CAPTURE` AppMessages request a one-frame renderer report containing
the actual formatted coordinate strings, selected object, UTC time, digit
bounds, and screen state for interaction checks. This adds one 32-bit capture
flag, no persistent buffers or heap
allocations, and no capture logging during normal operation. Errors retain an
`ASTRO_ERROR` marker in both modes so the CLI cannot hide their severity.
Code also occupies RAM on the smaller watches: current release builds use about
58 KB of their 64 KB budget (roughly 6.5 KB heap available), versus about 54 KB
before capture/error logging. Debug Flint has roughly 3.3 KB of heap available.
Check the SDK's per-platform memory report when changing instrumentation.
The runner fails on app errors, missing responses, wrong selections, malformed
coordinates, pointing outside tolerance, blank coordinate regions, overlapping
rows, clipping or incorrect rectangular/round alignment. It checks screenshots
at scale 6. Pointing tolerances are 0.08° (0.17° for independent minor-planet
vectors), accounting for the 0.1° display rounding and model error; host
positions correct for at most five seconds of emulator clock drift.
Elapsed fixture setup time is accounted for separately from clock drift.

Both suites write `summary.json` and per-platform/mode `report.json` files under the output directory
(default `build/emulator-tests`). Reports include pass/fail status, passed/total
case counts, formatted/expected values, tolerances, and the failing case/error.
Known quarantines appear explicitly in the `skipped` list, including their bug
reference; they are never counted as passed cases. Local runs continue through
the remaining platforms/modes after a failure and return a failing result.
Final reports are retained on **pass or fail**. Screenshots and logs are used
during testing, but only the failing case's screenshots and that run's
`app.log`/`commands.log` are retained. CI always uploads final reports and, on
failure, available evidence, including a failed summary if setup prevented the
runner from starting. Look for
`sanity-<mode>-<platform>` and `comprehensive-release-<platform>` artifacts.
The aggregate `sanity-report` and `comprehensive-report` artifacts also report
failed/skipped prerequisites, even when the emulator matrix could not start.

## Adding a catalog

Create a CSV under `catalogs/` using an existing file as a template. Supply
`title`, `short_title`, `provider`, `source` and `license` header comments and
the provider's columns; register the filename in `catalogs/catalogs.json`.
Fixed-object coordinates are J2000 degrees. IDs, names, titles, short titles
and source descriptions must fit the watch's 31-, 63-, 39-, 23- and 127-byte
UTF-8 limits respectively. The build validates the file and regenerates the
watch resource, phone settings credits and [CREDITS.md](CREDITS.md) from those
headers. Edit catalog headers rather than the generated credits document.

## Limitations and open issues

- GPS fixes without altitude use sea level and show a warning. The weather
  plugin's first saved location may differ from the observer's location;
  readings expire at the provider's `validUntilMs`, or after three hours when
  no expiry is supplied.
- Minor-planet elements cover 2026–2036; accuracy degrades outside the table.
  The results are intended for visual pointing, not precision navigation.
  Reference-vector tests cover sampled dates and locations, not every possible
  observing condition.
- The flint build is close to its 64 KB RAM limit, and resources are about
  121 KB—larger than the design estimates. Emulator filter searches can take
  about a second. Physical-watch performance, battery life, backlight color,
  and real Location-permission behavior still need verification.
- English is the only supplied UI language. The SDK linker emits RWX-segment
  warnings for the watch binaries.

## Design documentation

Read the [Design and As-Built Document 0.2](docs/DESIGN.md) for requirements,
architecture, algorithms, mockups and verification status.
