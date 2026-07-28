from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pyayfx.formats import decode_effect, encode_effect, load_afb, load_afx_pair, save_afb, save_afx_pair
from pyayfx.model import Bank, Effect, Frame


class ToneFlagRoundTripTests(unittest.TestCase):
    def make_effect(self) -> Effect:
        effect = Effect("split tone")
        effect.frames[0] = Frame(tone=0x123, volume=15, t=True)
        effect.frames[1] = Frame(tone=0x234, volume=14, t=False)
        effect.scc_frames[0] = Frame(tone=0x345, noise=7, volume=13, t=False)
        effect.scc_frames[1] = Frame(tone=0x456, volume=12, t=True)
        return effect

    def assert_split_tone_flags(self, effect: Effect) -> None:
        self.assertEqual([True, False], [effect.frames[0].t, effect.frames[1].t])
        self.assertEqual([False, True], [effect.scc_frames[0].t, effect.scc_frames[1].t])

    def test_effect_codec_keeps_each_tone_flag(self) -> None:
        source = self.make_effect()
        psg, _ = decode_effect(encode_effect(source))
        scc_source = Effect()
        scc_source.frames = [frame.clone() for frame in source.scc_frames]
        scc, _ = decode_effect(encode_effect(scc_source))

        self.assertEqual([True, False], [psg.frames[0].t, psg.frames[1].t])
        self.assertEqual([False, True], [scc.frames[0].t, scc.frames[1].t])

    def test_combined_afx_round_trip_keeps_psg_and_scc_tone_flags(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "effect.afx"
            save_afx_pair(self.make_effect(), path)
            self.assert_split_tone_flags(load_afx_pair(path))

    def test_paired_afb_round_trip_keeps_psg_and_scc_tone_flags(self) -> None:
        bank = Bank()
        bank.effects = [self.make_effect()]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bank.afb"
            save_afb(bank, path)
            loaded = load_afb(path)
            self.assert_split_tone_flags(loaded.effects[0])


if __name__ == "__main__":
    unittest.main()
