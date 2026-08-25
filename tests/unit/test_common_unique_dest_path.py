from utils.common import unique_dest_path


class TestUniqueDestPath:
    def test_no_collision_returns_original_name(self, tmp_path):
        result = unique_dest_path(tmp_path, 'foo.xlsx')
        assert result == tmp_path / 'foo.xlsx'

    def test_collision_appends_incrementing_suffix(self, tmp_path):
        (tmp_path / 'foo.xlsx').write_text('a')
        (tmp_path / 'foo(1).xlsx').write_text('b')

        result = unique_dest_path(tmp_path, 'foo.xlsx')

        assert result == tmp_path / 'foo(2).xlsx'

    def test_keeps_searching_past_multiple_collisions(self, tmp_path):
        (tmp_path / 'foo.xlsx').write_text('a')
        (tmp_path / 'foo(1).xlsx').write_text('b')
        (tmp_path / 'foo(2).xlsx').write_text('c')

        result = unique_dest_path(tmp_path, 'foo.xlsx')

        assert result == tmp_path / 'foo(3).xlsx'

    def test_preserves_suffix_when_appending_counter(self, tmp_path):
        (tmp_path / 'data.csv').write_text('a')

        result = unique_dest_path(tmp_path, 'data.csv')

        assert result == tmp_path / 'data(1).csv'
