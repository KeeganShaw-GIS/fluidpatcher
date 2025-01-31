"""A performance-oriented patch interface for FluidSynth

A Python interface for the FluidSynth software synthesizer that
allows combination of instrument settings, effects, sequences,
midi file players, etc. into performance patches that can be
quickly switched while playing. Patches are written in a rich,
human-readable YAML-based bank file format.

Includes:
- pfluidsynth.py: ctypes bindings to libfluidsynth and wrapper classes
    for FluidSynth's features/functions
- bankfiles.py: extensions to YAML and functions for parsing bank files

Requires:
- oyaml
- libfluidsynth
"""

__version__ = '0.8'

from pathlib import Path
from copy import deepcopy

from .bankfiles import parseyaml, renderyaml, SFPreset, MidiMessage, RouterRule
from .pfluidsynth import Synth


class FluidPatcher:
    """An interface for running FluidSynth using patches
    
    Provides methods for:

    - loading/saving the config file and bank files
    - applying/creating/copying/deleting patches
    - directly controlling the Synth by modifying fluidsettings,
      manually adding router rules, and sending MIDI events
    - loading a single soundfont and browsing its presets
    
    Attributes:
      midi_callback: a function that takes a pfluidsynth.Midisignal instance
        as its argument. Will be called when MIDI events are received or
        custom router rules are triggered. This allows scripts to define
        and handle their own custom router rules and/or monitor incoming events.    
        MidiSignal events have `type`, `chan`, `par1`, and `par2` events matching
        the triggering event. MidiSignals generated by rules have extra attributes
        corresponding to the rule parameters, plus a `val` attribute that is the
        result of parameter routing. Rules with a `patch` parameter will be modified
        by FluidPatcher so that the `patch` attribute corresponds to the patch index.
        If `patch` is -1, `val` is set to the patch increment.
    
    See the documentation for information on bank file format.
    """

    def __init__(self, cfgfile='', **fluidsettings):
        """Creates FluidPatcher and starts FluidSynth
        
        Starts fluidsynth using settings found in yaml-formatted `cfgfile`.
        Settings passed via `fluidsettings` will override those in config file.
        See https://www.fluidsynth.org/api/fluidsettings.xml for a
        full list and explanation of settings. See documentation
        for config file format.
        
        Args:
          cfgfile: path to config file
          fluidsettings: additional fluidsettings as keyword list
        """
        self.cfgfile = Path(cfgfile) if cfgfile else None
        self.cfg = {}
        self.read_config()
        self.bank = {}
        self.soundfonts = set()
        self.fsynth = Synth(**{**self.cfg.get('fluidsettings', {}), **fluidsettings})
        self.fsynth.midi_callback = self._midisignal_handler
        self.max_channels = self.fluidsetting_get('synth.midi-channels')
        self.patchcord = {'patchcordxxx': {'lib': self.plugindir / 'patchcord', 'audio': 'mono'}}
        self.midi_callback = None

    @property
    def currentbank(self):
        """a Path object pointing to the current bank file"""
        return Path(self.cfg['currentbank']) if 'currentbank' in self.cfg else ''

    @property
    def bankdir(self):
        """Path to bank files"""
        return Path(self.cfg.get('bankdir', 'banks')).resolve()

    @property
    def sfdir(self):
        """Path to soundfonts"""
        return Path(self.cfg.get('soundfontdir', 'sf2')).resolve()

    @property
    def mfilesdir(self):
        """Path to MIDI files"""
        return Path(self.cfg.get('mfilesdir', '')).resolve()

    @property
    def plugindir(self):
        """Path to LADSPA effects"""
        return Path(self.cfg.get('plugindir', '')).resolve()

    @property
    def patches(self):
        """List of patch names in the current bank"""
        return list(self.bank.get('patches', {})) if self.bank else []

    def read_config(self):
        """Read configuration from `cfgfile` set on creation

        Returns: the raw contents of the config file
        """
        if self.cfgfile == None:
            # If no cfgfile was provided return a representation of self.cfg
            return renderyaml(self.cfg)
        raw = self.cfgfile.read_text()
        self.cfg = parseyaml(raw)
        return raw

    def write_config(self, raw=''):
        """Write current config to file
        
        Write current configuration stored in `cfg` to file.
        If raw is provided and parses, write that exactly.

        Args:
          raw: exact text to write
        """
        if raw:
            c = parseyaml(raw)
            self.cfg = c
            if self.cfgfile: self.cfgfile.write_text(raw)
        else:
            self.cfgfile.write_text(renderyaml(self.cfg))

    def load_bank(self, bankfile='', raw=''):
        """Load a bank from a file or from raw yaml text

        Parses a yaml stream from a string or file and stores as a
        nested collection of dict and list objects. The top-level
        dict must have at minimum a `patches` element or an error
        is raised. If loaded from a file successfully, that file
        is set as `currentbank` in the config - call write_config()
        to make it persistent.

        Upon loading, resets the synth, loads all necessary soundfonts,
        and applies settings in the `init` element. Returns the yaml stream
        as a string. If called with no arguments, resets the synth and
        restores the current bank from memory.

        Args:
          bankfile: bank file to load, absolute or relative to `bankdir`
          raw: string to parse directly

        Returns: yaml stream that was loaded
        """
        if bankfile:
            try:
                raw = (self.bankdir / bankfile).read_text()
                bank = parseyaml(raw)
            except:
                if Path(bankfile).as_posix() == self.cfg['currentbank']:
                    self.cfg.pop('currentbank', None)
                raise
            else:
                self.bank = bank
                self.cfg['currentbank'] = Path(bankfile).as_posix()
        elif raw:
            bank = parseyaml(raw)
            self.bank = bank
        self._reset_synth()
        self._refresh_bankfonts()
        for zone in self.bank, *self.bank.get('patches', {}).values():
            for midi in zone.get('midiplayers', {}).values():
                midi['file'] = self.mfilesdir / midi['file']
            for fx in zone.get('ladspafx', {}).values():
                fx['lib'] = self.plugindir / fx['lib']
        for syx in self.bank.get('init', {}).get('sysex', []):
            self.fsynth.send_sysex(syx)
        for opt, val in self.bank.get('init', {}).get('fluidsettings', {}).items():
            self.fluidsetting_set(opt, val)
        for msg in self.bank.get('init', {}).get('messages', []):
            self.send_event(msg)
        return raw

    def save_bank(self, bankfile, raw=''):
        """Save a bank file
        
        Saves the current bank in memory to `bankfile` after rendering it as
        a yaml stream. If `raw` is provided, it is parsed as the new bank and
        its exact contents are written to the file.

        Args:
          bankfile: file to save, absolute or relative to `bankdir`
          raw: exact text to save
        """
        if raw:
            bank = parseyaml(raw)
            self.bank = bank
        else:
            raw = renderyaml(self.bank)
        (self.bankdir / bankfile).write_text(raw)
        self.cfg['currentbank'] = Path(bankfile).as_posix()

    def apply_patch(self, patch):
        """Select a patch and apply its settings

        Read the settings for the patch specified by index or name and combine
        them with bank-level settings. Select presets on specified channels and
        unsets others, clears router rules and applies new ones, activates 
        players and effects and deactivates unused ones, send messages, and
        applies fluidsettings. Patch settings are applied after bank settings.
        If the specified patch isn't found, only bank settings are applied.

        Args:
          patch: patch index or name

        Returns: a list of warnings, if any
        """
        warnings = []
        patch = self._resolve_patch(patch)
        def mrg(kw):
            try: return self.bank.get(kw, {}) | patch.get(kw, {})
            except TypeError: return self.bank.get(kw, []) + patch.get(kw, [])
        # presets
        for ch in range(1, self.max_channels + 1):
            if p := self.bank.get(ch) or patch.get(ch):
                if not self.fsynth.program_select(ch, self.sfdir / p.sfont, p.bank, p.prog):
                    warnings.append(f"Unable to select preset {p} on channel {ch}")
            else: self.fsynth.program_unset(ch)
        # sysex
        for syx in mrg('sysex'):
            self.fsynth.send_sysex(syx)
        # fluidsettings
        for opt, val in mrg('fluidsettings').items():
            self.fluidsetting_set(opt, val)
        # sequencers, arpeggiators, midiplayers
        self.fsynth.players_clear(save=[*mrg('sequencers'), *mrg('arpeggiators'), *mrg('midiplayers')])
        for name, seq in mrg('sequencers').items():
            self.fsynth.sequencer_add(name, **seq)
        for name, arp in mrg('arpeggiators').items():
            self.fsynth.arpeggiator_add(name, **arp)
        for name, midi in mrg('midiplayers').items():
            self.fsynth.midiplayer_add(name, **midi)
        # ladspa effects
        self.fsynth.fxchain_clear(save=mrg('ladspafx'))
        for name, fx in (mrg('ladspafx') | self.patchcord).items():
            self.fsynth.fxchain_add(name, **fx)
        self.fsynth.fxchain_connect()
        # router rules -- invert b/c fluidsynth applies rules last-first
        self.fsynth.router_default()
        rules = [*mrg('router_rules')][::-1]
        if 'clear' in rules:
            self.fsynth.router_clear()
            rules = rules[:rules.index('clear')]
        for rule in rules:
            rule.add(self.fsynth.router_addrule)
        # midi messages
        for msg in mrg('messages'):
            self.send_event(msg)
        return warnings

    def add_patch(self, name, addlike=None):
        """Add a new patch

        Create a new empty patch, or one that copies all settings
        other than instruments from an existing patch

        Args:
          name: a name for the new patch
          addlike: number or name of an existing patch

        Returns: the index of the new patch
        """
        if 'patches' not in self.bank: self.bank['patches'] = {}
        self.bank['patches'][name] = {}
        if addlike:
            addlike = self._resolve_patch(addlike)
            for x in addlike:
                if not isinstance(x, int):
                    self.bank['patches'][name][x] = deepcopy(addlike[x])
        return self.patches.index(name)

    def update_patch(self, patch):
        """Update the current patch

        Instruments and controller values can be changed by program change (PC)
        and continuous controller (CC) messages, but these will not persist
        in the patch unless this function is called. Settings can be saved to
        a new patch by first calling add_patch(), then update_patch() on the
        new patch. The bank file must be saved for updated patches to become
        permanent.

        Args:
          patch: index or name of the patch to update
        """
        patch = self._resolve_patch(patch)
        messages = set(patch.get('messages', []))
        for channel in range(1, self.max_channels + 1):
            info = self.fsynth.program_info(channel)
            if not info:
                patch.pop(channel, None)
                continue
            sfont, bank, prog = info
            sfrel = Path(sfont).relative_to(self.sfdir).as_posix()
            patch[channel] = SFPreset(sfrel, bank, prog)
            for cc, default in enumerate(_CC_DEFAULTS):
                if default < 0: continue
                val = self.fsynth.get_cc(channel, cc)
                if val != default:
                    messages.add(MidiMessage('cc', channel, cc, val))
        if messages:
            patch['messages'] = list(messages)

    def delete_patch(self, patch):
        """Delete a patch from the bank in memory

        Bank file must be saved for deletion to be permanent

        Args:
          patch: index or name of the patch to delete
        """
        if isinstance(patch, int):
            name = self.patches[patch]
        else:
            name = patch
        del self.bank['patches'][name]
        self._refresh_bankfonts()

    def fluidsetting_get(self, opt):
        """Get the current value of a FluidSynth setting

        Args:
          opt: setting name

        Returns: the setting's current value as float, int, or str
        """
        return self.fsynth.get_setting(opt)

    def fluidsetting_set(self, opt, val, patch=None):
        """Change a FluidSynth setting

        Modifies a FluidSynth setting. Settings without a "synth." prefix
        are ignored. If `patch` is provided, these settings are also added to
        the current bank in memory at bank level, and any conflicting
        settings are removed from the specified patch - which should ideally
        be the current patch so that the changes can be heard. The bank file
        must be saved for the changes to become permanent.

        Args:
          opt: setting name
          val: new value to set, type depends on setting
          patch: patch name or index
        """
        if not opt.startswith('synth.'): return
        self.fsynth.setting(opt, val)
        if patch != None:
            if 'fluidsettings' not in self.bank:
                self.bank['fluidsettings'] = {}
            self.bank['fluidsettings'][opt] = val
            patch = self._resolve_patch(patch)
            if 'fluidsettings' in patch and opt in patch['fluidsettings']:
                del patch['fluidsettings'][opt]

    def add_router_rule(self, **pars):
        """Add a router rule to the Synth

        Directly add a router rule to the Synth. This rule will be added
        after the current bank- and patch-level rules. The rule is not
        saved to the bank, and will disappear if a patch is applied
        or the synth is reset.

        Returns:
          pars: router rule as a set of key=value pairs
        """
        RouterRule(**pars).add(self.fsynth.router_addrule)

    def send_event(self, msg=None, type='note', chan=0, par1=0, par2=None):
        """Send a MIDI event to the Synth

        Sends a MidiMessage, or constructs one from a bank file-styled string
        (<type>:<channel>:<par1>:<par2>) or keywords and sends it
        to the Synth, which will apply all current router rules.

        Args:
          msg: MidiMessage instance or string
          type: event type as string
          chan: MIDI channel
          par1: first parameter, integer or note name
          par2: second parameter for valid types
        """
        if isinstance(msg, str):
            msg = parseyaml(msg)
        elif msg == None:
            msg = MidiMessage(type, chan, par1, par2)
        self.fsynth.send_event(*msg)

    def solo_soundfont(self, soundfont):
        """Suspend the current bank and load a single soundfont

        Resets the Synth, loads a single soundfont, and creates router
        rules that route messages from all channels to channel 1.
        Scans through each bank and program in order and retrieves the
        preset name. After this, select_sfpreset() can be used to play
        any instrument in the soundfont. Call load_bank() with no
        arguments to restore the current bank.

        Args:
          soundfont: soundfont file to load, absolute or relative to `sfdir`
        
        Returns: a list of (bank, prog, name) tuples for each preset
        """
        for sfont in self.soundfonts - {soundfont}:
            self.fsynth.unload_soundfont(self.sfdir / sfont)
        if {soundfont} - self.soundfonts:
            if not self.fsynth.load_soundfont(self.sfdir / soundfont):
                self.soundfonts = set()
                return []
        self.soundfonts = {soundfont}
        self._reset_synth()
        for channel in range(1, self.max_channels + 1):
            self.fsynth.program_unset(channel)
        for type in 'note', 'cc', 'pbend', 'cpress', 'kpress':
            self.add_router_rule(type=type, chan=f"2-{self.max_channels}=1")
        return self.fsynth.get_sfpresets(self.sfdir / soundfont)
        
    def select_sfpreset(self, sfont, bank, prog, *_):
        """Select a preset on channel 1

        Call to select one of the presets in the soundfont loaded
        by solo_soundfount(). The variable-length garbage argument
        allows this function to be called by unpacking one of the
        tuples returned by solo_soundfont().

        Args:
          sfont: the soundfont file loaded by solo_soundfont(),
            absolute or relative to `sfdir`
          bank: the bank to select
          prog: the program to select from bank

        Returns: a list of warnings, empty if none
        """
        if sfont not in self.soundfonts:
            return [f"{str(sfont)} is not loaded"]
        if self.fsynth.program_select(1, self.sfdir / sfont, bank, prog):
            return []
        else: return [f"Unable to select preset {str(sfont)}:{bank:03d}:{prog:03d}"]

    def _midisignal_handler(self, sig):
        if 'patch' in sig:
            if sig.patch in self.patches:
                sig.patch = self.patches.index(sig.patch)
            elif sig.patch == 'select':
                sig.patch = int(sig.val) % len(self.patches)
            elif sig.patch[-1] in '+-':
                sig.val = int(sig.patch[-1] + sig.patch[:-1])
                sig.patch = -1
            else:
                sig.patch = -1
                sig.val = 0
        if self.midi_callback: self.midi_callback(sig)

    def _refresh_bankfonts(self):
        sfneeded = set()
        for zone in self.bank, *self.bank.get('patches', {}).values():
            for sfont in [zone[ch].sfont for ch in zone if isinstance(ch, int)]:
                sfneeded.add(sfont)
        missing = set()
        for sfont in self.soundfonts - sfneeded:
            self.fsynth.unload_soundfont(self.sfdir / sfont)
        for sfont in sfneeded - self.soundfonts:
            if not self.fsynth.load_soundfont(self.sfdir / sfont):
                missing.add(sfont)
        self.soundfonts = sfneeded - missing

    def _resolve_patch(self, patch):
        if isinstance(patch, int):
            if 0 <= patch < len(self.patches):
                patch = self.patches[patch]
            else: patch = {}
        if isinstance(patch, str):
            patch = self.bank.get('patches', {}).get(patch, {})
        return patch

    def _reset_synth(self):
        self.fsynth.players_clear()
        self.fsynth.fxchain_clear()
        self.fsynth.router_default()
        self.fsynth.reset()
        for opt, val in {**_SYNTH_DEFAULTS, **self.cfg.get('fluidsettings', {})}.items():
            self.fluidsetting_set(opt, val)


_CC_DEFAULTS = [0] * 120
_CC_DEFAULTS[0] = -1             # bank select
_CC_DEFAULTS[7] = 100            # volume
_CC_DEFAULTS[8] = 64             # balance
_CC_DEFAULTS[10] = 64            # pan
_CC_DEFAULTS[11] = 127           # expression
_CC_DEFAULTS[32] = -1            # bank select LSB
_CC_DEFAULTS[43] = 127           # expression LSB
_CC_DEFAULTS[70:80] = [64] * 10  # sound controls
_CC_DEFAULTS[84] = 255           # portamento control
_CC_DEFAULTS[96:102] = [-1] * 6  # RPN/NRPN controls

_SYNTH_DEFAULTS = {'synth.chorus.active': 1, 'synth.reverb.active': 1,
                  'synth.chorus.depth': 8.0, 'synth.chorus.level': 2.0,
                  'synth.chorus.nr': 3, 'synth.chorus.speed': 0.3,
                  'synth.reverb.damp': 0.0, 'synth.reverb.level': 0.9,
                  'synth.reverb.room-size': 0.2, 'synth.reverb.width': 0.5,
                  'synth.gain': 0.2}
