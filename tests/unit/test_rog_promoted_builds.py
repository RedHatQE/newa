"""Tests for parsing promoted builds from OSCI Bot comments on RoG merge requests."""

from unittest import mock

from newa.services.rog_service import RoGTool


def make_gm(note_bodies):
    """Create a mock merge request object with the given note bodies."""
    notes = []
    for body in note_bodies:
        note = mock.MagicMock()
        note.body = body
        notes.append(note)
    gm = mock.MagicMock()
    gm.notes.list.return_value = notes
    return gm


class TestGetMrPromotedBuilds:

    def test_parses_promotion_comment(self):
        gm = make_gm([
            'Promoted Build(s)\n\n'
            '    rhel: opencryptoki-3.27.0-1.el10_3.1,draft_4170018'
            ' -> opencryptoki-3.27.0-1.el10_3.1\n',
            ])

        promotions = RoGTool(token='token').get_mr_promoted_builds(gm)

        assert promotions == {'opencryptoki-3.27.0-1.el10_3.1,draft_4170018'}

    def test_parses_real_promotion_comment(self):
        """Test with a comment as really posted by OSCI Bot (markdown)."""
        gm = make_gm([
            '<!--PUSH_PIPELINE_COMMENT-->\n'
            '**Push pipeline [3121007412](https://gitlab.com/pipeline/3121007412)**\n'
            '\n\n\n\n'
            '**Promoted Build(s)**\n\n'
            '- **rhel:** opencryptoki-3.27.0-1.el10_3.1,draft_4170018'
            ' -> [opencryptoki-3.27.0-1.el10_3.1](https://brewweb.engineering.'
            'redhat.com/brew/search?terms=opencryptoki-3.27.0-1.el10_3.1'
            '&type=build&match=exact)\n'
            '\n\n\n\n\n\n'
            '**Moving Build(s)**\n\n'
            '- **rhel:** rhel-10.3-z-draft -> rhel-10.3-z-candidate\n',
            ])

        promotions = RoGTool(token='token').get_mr_promoted_builds(gm)

        assert promotions == {'opencryptoki-3.27.0-1.el10_3.1,draft_4170018'}

    def test_ignores_unrelated_comments(self):
        gm = make_gm([
            'Some random comment',
            'retrigger',
            ])

        promotions = RoGTool(token='token').get_mr_promoted_builds(gm)

        assert promotions == set()

    def test_notes_failure_returns_no_promotions(self):
        gm = mock.MagicMock()
        gm.notes.list.side_effect = Exception('temporary gitlab failure')

        promotions = RoGTool(token='token').get_mr_promoted_builds(gm)

        assert promotions == set()

    def test_multiple_promotions(self):
        gm = make_gm([
            'Promoted Build(s)\n\n'
            '    rhel: one-1.0-1.el10,draft_1 -> one-1.0-1.el10\n'
            '    rhel: two-2.0-1.el10,draft_2 -> two-2.0-1.el10\n',
            ])

        promotions = RoGTool(token='token').get_mr_promoted_builds(gm)

        assert promotions == {'one-1.0-1.el10,draft_1', 'two-2.0-1.el10,draft_2'}
