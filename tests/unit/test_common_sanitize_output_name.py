from utils.common import sanitize_output_name


class TestSanitizeOutputName:
    def test_no_whitespace_unchanged(self):
        assert sanitize_output_name('foo_bar') == 'foo_bar'

    def test_single_space_replaced_with_underscore(self):
        assert sanitize_output_name('foo bar') == 'foo_bar'

    def test_multiple_consecutive_spaces_collapse_to_one_underscore(self):
        assert sanitize_output_name('foo   bar') == 'foo_bar'

    def test_multiple_separate_spaces_each_replaced(self):
        assert sanitize_output_name('foo bar baz') == 'foo_bar_baz'

    def test_leading_and_trailing_whitespace_is_stripped_not_underscored(self):
        assert sanitize_output_name('  foo bar  ') == 'foo_bar'

    def test_tabs_and_newlines_are_also_collapsed(self):
        assert sanitize_output_name('foo\tbar\nbaz') == 'foo_bar_baz'

    def test_real_world_provider_file_name(self):
        assert (
            sanitize_output_name('SNE_batch1_PAXgeneDNA_mitochondrial copy number assay results_rev')
            == 'SNE_batch1_PAXgeneDNA_mitochondrial_copy_number_assay_results_rev'
        )
