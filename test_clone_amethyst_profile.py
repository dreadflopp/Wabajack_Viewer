"""Integration tests using tiny synthetic Amethyst installations only."""
from pathlib import Path
from unittest.mock import patch
import importlib.util
import io
import json
import os
import sqlite3
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location('profile_cloner', HERE / 'clone_amethyst_profile.py')
cloner = importlib.util.module_from_spec(SPEC)
import sys
sys.modules[SPEC.name] = cloner
SPEC.loader.exec_module(cloner)


class CloneTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='.clone-script-test-', dir=HERE)
        self.root = Path(self.temporary.name)
        self.staging = self.root / 'Amethyst/Skyrim Special Edition'
        self.source = self.staging / 'profiles/Original'
        self.source.mkdir(parents=True)
        self.config = self.root / 'config'
        (self.config / 'games/Skyrim Special Edition').mkdir(parents=True)
        self.game = self.root / 'original-game'
        (self.game / 'Data').mkdir(parents=True)
        (self.game / 'SkyrimSE.exe').write_bytes(b'fixture-game')
        (self.game / 'Data/Skyrim.esm').write_bytes(b'fixture-master')
        (self.staging / 'mods').mkdir()
        self.write_json(self.config / 'games/Skyrim Special Edition/paths.json',
                        {'game_path': str(self.game), 'staging_path': str(self.staging)})
        self.write_json(self.config / 'games/Skyrim Special Edition/deploy_state.json', {'deploy_active': False})
        self.write_json(self.source / 'profile_state.json',
                        {'profile_settings': {'profile_specific_mods': True, 'game_path': str(self.game)},
                         'excluded_mod_files': {'One Mod': ['hidden.dds']}})
        (self.source / 'mods/One Mod').mkdir(parents=True)
        (self.source / 'mods/One Mod/test.esp').write_bytes(b'fixture-plugin')
        (self.source / 'modlist.txt').write_text('+One Mod\n')
        (self.source / 'plugins.txt').write_text('*test.esp\n')
        (self.source / 'loadorder.txt').write_text('Skyrim.esm\ntest.esp\n')
        (self.source / 'Saves').mkdir()
        (self.source / 'Saves/save.ess').write_bytes(b'fixture-save')
        (self.source / 'filegraph.cache').write_bytes(b'old deployment cache')
        (self.source / 'deploy_snapshot.txt').write_text('old snapshot')
        self.no_app = patch.object(cloner, 'running_amethyst', return_value=[])
        self.no_app.start()

    def tearDown(self):
        self.no_app.stop()
        self.temporary.cleanup()

    @staticmethod
    def write_json(path, value):
        path.write_text(json.dumps(value))

    def args(self, *extra):
        return cloner.parser().parse_args(['--staging', str(self.staging), '--config-dir', str(self.config),
                                          '--source', 'Original', '--name', 'Copy', *extra])

    def clone(self, args=None):
        args = args or self.args()
        plan = cloner.build_plan(args)
        report = cloner.apply_plan(plan, args, progress=lambda message: None)
        return plan, report

    def test_ordinary_profile_is_independent_and_lists_are_preserved(self):
        plan, report = self.clone()
        self.assertEqual(report['status'], 'passed')
        self.assertEqual((plan.destination / 'modlist.txt').read_bytes(), (self.source / 'modlist.txt').read_bytes())
        self.assertFalse((plan.destination / 'filegraph.cache').exists())
        self.assertFalse((plan.destination / 'deploy_snapshot.txt').exists())
        new = plan.destination / 'mods/One Mod/test.esp'
        old = self.source / 'mods/One Mod/test.esp'
        self.assertNotEqual(new.stat().st_ino, old.stat().st_ino)
        new.write_bytes(b'changed')
        self.assertEqual(old.read_bytes(), b'fixture-plugin')
        (plan.game / 'SkyrimSE.exe').write_bytes(b'changed-game')
        self.assertEqual((self.game / 'SkyrimSE.exe').read_bytes(), b'fixture-game')
        self.assertEqual((plan.destination / 'Saves/save.ess').read_bytes(), b'fixture-save')

    def test_dry_run_writes_nothing(self):
        before = sorted(str(p.relative_to(self.root)) for p in self.root.rglob('*'))
        plan = cloner.build_plan(self.args())
        self.assertEqual(plan.preview()['mode'], 'dry-run')
        after = sorted(str(p.relative_to(self.root)) for p in self.root.rglob('*'))
        self.assertEqual(before, after)

    def test_command_line_copy_creates_profile_without_review_tracking(self):
        output = io.StringIO()
        with patch('sys.stdout', output):
            result = cloner.main(['--staging', str(self.staging), '--config-dir', str(self.config),
                                  '--source', 'Original', '--name', 'Copy', '--apply'])
        self.assertEqual(result, 0)
        self.assertTrue((self.staging / 'profiles/Copy/modlist.txt').is_file())
        self.assertNotIn('review_project', output.getvalue())

    def test_wabbajack_symlink_store_tools_and_windows_paths_are_detached(self):
        root = self.staging / '.wj/example/root'
        root.mkdir(parents=True)
        (self.source / 'mods').rename(root / 'mods')
        (self.source / 'mods').symlink_to(root / 'mods', target_is_directory=True)
        self.game.rename(root / 'Game Root')
        game = root / 'Game Root'
        tool = root / 'tools/ExampleTool/tool.exe'
        tool.parent.mkdir(parents=True)
        tool.write_bytes(b'fixture-tool')
        (tool.parent / 'settings.ini').write_text('GamePath=D:\\Original\\Game Root\nOutput=' + cloner.wine(root / 'mods') + '\n')
        self.write_json(self.source / 'profile_state.json', {'profile_settings': {
            'profile_specific_mods': True, 'game_path': str(game), 'wabbajack_install_id': 'id',
            'wabbajack_directory': str(root.parent), 'wabbajack_profile': 'Original'},
            'custom_exes': [str(tool)], 'wabbajack_working_directories': {str(tool): str(tool.parent)}})
        plan, _ = self.clone()
        state = cloner.read_json(plan.destination / 'profile_state.json')
        self.assertNotIn('wabbajack_install_id', state['profile_settings'])
        self.assertNotIn('wabbajack_working_directories', state)
        self.assertEqual(state['custom_exes'], [str(plan.destination / 'tools/ExampleTool/tool.exe')])
        text = (plan.destination / 'tools/ExampleTool/settings.ini').read_text()
        self.assertNotIn('.wj', text)
        self.assertIn(cloner.wine(plan.game), text)

    def test_shared_mod_profile_copies_only_listed_mods_and_shared_root_files(self):
        (self.source / 'mods/One Mod').rename(self.staging / 'mods/One Mod')
        (self.staging / 'mods/Unrelated').mkdir()
        (self.staging / 'Root_Folder').mkdir()
        (self.staging / 'Root_Folder/loader.dll').write_bytes(b'loader')
        self.write_json(self.source / 'profile_state.json', {'profile_settings': {}})
        plan, _ = self.clone()
        self.assertTrue((plan.destination / 'mods/One Mod/test.esp').is_file())
        self.assertFalse((plan.destination / 'mods/Unrelated').exists())
        self.assertEqual((plan.destination / 'Root_Folder/loader.dll').read_bytes(), b'loader')

    def test_existing_targets_and_nested_game_roots_are_rejected(self):
        target = self.staging / 'profiles/Copy'
        target.mkdir()
        (target / 'important').write_text('untouched')
        with self.assertRaises(cloner.CloneError):cloner.build_plan(self.args())
        self.assertEqual((target / 'important').read_text(), 'untouched')
        target.joinpath('important').unlink();target.rmdir()
        with self.assertRaises(cloner.CloneError):
            cloner.build_plan(self.args('--game-root', str(self.staging / 'profiles/Game')))
        with self.assertRaises(cloner.CloneError):
            cloner.build_plan(self.args('--name', '../escape'))

    def test_deployed_source_and_external_symlinks_are_rejected(self):
        (self.game / 'Data/.mm_deployed').touch()
        with self.assertRaises(cloner.CloneError):cloner.build_plan(self.args())
        (self.game / 'Data/.mm_deployed').unlink()
        outside = self.root / 'not-owned.txt';outside.write_text('external')
        (self.source / 'mods/One Mod/external').symlink_to(outside)
        with self.assertRaises(cloner.CloneError):cloner.build_plan(self.args())

    def test_internal_symlinks_are_rebased_into_copy(self):
        original = self.source / 'mods/One Mod/test.esp'
        (self.source / 'mods/One Mod/alias.esp').symlink_to(original)
        plan, _ = self.clone()
        alias = plan.destination / 'mods/One Mod/alias.esp'
        self.assertEqual(alias.resolve(), plan.destination / 'mods/One Mod/test.esp')

    def test_failed_second_publish_rolls_back_both_copies(self):
        args = self.args();plan = cloner.build_plan(args)
        real = cloner.rename_new;calls = []
        def fail_second(old, new):
            calls.append((old, new))
            if len(calls) == 2:raise OSError('simulated second rename failure')
            return real(old, new)
        with patch.object(cloner, 'rename_new', side_effect=fail_second):
            with self.assertRaises(OSError):cloner.apply_plan(plan, args, progress=lambda message: None)
        self.assertFalse(plan.destination.exists())
        self.assertFalse(plan.game.exists())
        self.assertFalse(list(self.staging.parent.glob('.game-clone-*')))
        self.assertFalse(list((self.staging / 'profiles').glob('.profile-clone-*')))

    def test_existing_ordinary_clone_can_be_cloned_again_and_no_saves_works(self):
        first, _ = self.clone()
        args = self.args('--source', 'Copy', '--name', 'Second Copy', '--no-saves')
        second, _ = self.clone(args)
        self.assertFalse((second.destination / 'Saves/save.ess').exists())
        state = cloner.read_json(second.destination / 'profile_state.json')
        self.assertEqual(state['profile_settings']['game_path'], str(second.game))
        self.assertTrue(state['profile_settings']['profile_saves'])
        self.assertNotEqual(first.game, second.game)

    def test_live_mo2_compatibility_stub_tracks_new_profile(self):
        path = self.source / 'mods/One Mod/PGPatcher/cfg/settings.json'
        path.parent.mkdir(parents=True)
        self.write_json(path, {'params': {'game': {'dir': cloner.wine(self.game), 'type': 0},
                                         'modmanager': {'type': 2, 'mo2instancedir': 'D:\\Old'}}})
        plan, _ = self.clone()
        text = (plan.destination / 'tools/amm_mo2_dummy/ModOrganizer.ini').read_text()
        self.assertIn('selected_profile=@ByteArray(Copy)', text)
        self.assertIn('profiles_directory=' + cloner.wine(plan.destination.parent), text)
        params = cloner.read_json(plan.destination / path.relative_to(self.source))['params']
        self.assertEqual(params['game']['dir'], cloner.wine(plan.game))
        self.assertEqual(params['modmanager']['mo2instancedir'], cloner.wine(plan.destination / 'tools/amm_mo2_dummy'))

    def test_external_tool_full_copy_and_profile_case_collision(self):
        tool = self.root / 'external-tool/tool.exe'
        tool.parent.mkdir();tool.write_bytes(b'external-tool')
        (tool.parent / 'dependency.dll').write_bytes(b'dependency')
        state = cloner.read_json(self.source / 'profile_state.json')
        state['custom_exes'] = [str(tool)]
        self.write_json(self.source / 'profile_state.json', state)
        plan, result = self.clone(self.args('--copy-mode', 'full', '--copy-external-tools'))
        self.assertEqual(result['reflinked_files'], 0)
        copied = Path(cloner.read_json(plan.destination / 'profile_state.json')['custom_exes'][0])
        self.assertTrue(copied.is_relative_to(plan.destination))
        self.assertEqual((copied.parent / 'dependency.dll').read_bytes(), b'dependency')
        copied.write_bytes(b'modified-tool')
        self.assertEqual(tool.read_bytes(), b'external-tool')
        with self.assertRaises(cloner.CloneError):cloner.build_plan(self.args('--name', 'copy'))

    def test_running_app_blocks_apply_without_creating_destinations(self):
        args = self.args();plan = cloner.build_plan(args)
        with patch.object(cloner, 'running_amethyst', return_value=[123]):
            with self.assertRaises(cloner.CloneError):cloner.apply_plan(plan, args)
        self.assertFalse(plan.destination.exists())
        self.assertFalse(plan.game.exists())

    def test_rebasing_respects_path_boundaries_and_double_backslashes(self):
        plan = cloner.build_plan(self.args());rewrite = cloner.Rebaser(plan)
        old = str(self.game)
        self.assertEqual(rewrite.text(old + '-unrelated/file'), old + '-unrelated/file')
        old_wine = cloner.wine(self.game).replace('\\', '\\\\')
        new_wine = cloner.wine(plan.game).replace('\\', '\\\\')
        self.assertEqual(rewrite.text(old_wine + '\\\\Data'), new_wine + '\\\\Data')

    def test_other_list_title_profile_name_and_stock_game_layout(self):
        root = self.staging / '.wj/different-list/root'
        root.mkdir(parents=True)
        (self.source / 'mods').rename(root / 'mods')
        (self.source / 'mods').symlink_to(root / 'mods', target_is_directory=True)
        self.game.rename(root / 'Stock Game')
        game = root / 'Stock Game'
        database = sqlite3.connect(root.parent / 'state.sqlite')
        database.execute('CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT)')
        database.execute('INSERT INTO metadata VALUES (?,?)', ('name', json.dumps('A Different List')))
        database.commit();database.close()
        tool = root / 'tools/ExampleTool/tool.exe'
        tool.parent.mkdir(parents=True);tool.write_bytes(b'tool')
        (tool.parent / 'settings.ini').write_text(
            'GamePath=F:\\A Different List\\Stock Game\n'
            'ProfilePath=F:\\A Different List\\profiles\\Performance\n')
        self.write_json(self.source / 'profile_state.json', {'profile_settings': {
            'profile_specific_mods': True, 'game_path': str(game),
            'wabbajack_install_id': 'another-id', 'wabbajack_directory': str(root.parent),
            'wabbajack_profile': 'Performance'}, 'custom_exes': [str(tool)]})
        plan, _ = self.clone()
        text = (plan.destination / 'tools/ExampleTool/settings.ini').read_text()
        self.assertIn('GamePath=' + cloner.wine(plan.game), text)
        self.assertIn('ProfilePath=' + cloner.wine(plan.destination), text)
        self.assertNotIn('A Different List', text)
        state = cloner.read_json(plan.destination / 'profile_state.json')
        self.assertNotIn('wabbajack_install_id', state['profile_settings'])

    def test_explicit_authored_windows_root_can_differ_from_every_profile_name(self):
        path = self.source / 'mods/One Mod/settings.ini'
        path.write_text('GamePath=Q:\\CompletelyDifferentInstall\\original-game\n'
                        'Mods=Q:\\CompletelyDifferentInstall\\mods\n')
        plan, _ = self.clone(self.args('--authored-root', 'Q:\\CompletelyDifferentInstall'))
        text = (plan.destination / path.relative_to(self.source)).read_text()
        self.assertIn('GamePath=' + cloner.wine(plan.game), text)
        self.assertIn('Mods=' + cloner.wine(plan.destination / 'mods'), text)


if __name__ == '__main__':
    unittest.main(verbosity=2)
