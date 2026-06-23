"""Security-Regression: ACL-Leser sind fail-closed (defekt/fehlt -> kein Zugriff)."""
import json
import pytest

from connectors.acl_readers import SidecarAclReader, CompositeAclReader, PrefixAclReader


def test_fs_sidecar_missing_is_empty(tmp_path):
    f = tmp_path / "doc.txt"
    f.write_text("inhalt")
    assert SidecarAclReader().groups_for(str(f)) == []   # kein Sidecar -> kein Zugriff


def test_fs_sidecar_corrupt_is_empty(tmp_path):
    f = tmp_path / "doc.txt"
    f.write_text("inhalt")
    (tmp_path / "doc.txt.acl.json").write_text("{ kaputt ")
    assert SidecarAclReader().groups_for(str(f)) == []   # defekt -> fail-closed


def test_fs_sidecar_valid(tmp_path):
    f = tmp_path / "doc.txt"
    f.write_text("inhalt")
    (tmp_path / "doc.txt.acl.json").write_text(json.dumps({"groups": ["SALES"]}))
    assert SidecarAclReader().groups_for(str(f)) == ["SALES"]


def test_fs_composite_sidecar_beats_prefix(tmp_path):
    f = tmp_path / "SALES_x.txt"
    f.write_text("inhalt")
    (tmp_path / "SALES_x.txt.acl.json").write_text(json.dumps({"groups": ["PROJECT_PHOENIX"]}))
    reader = CompositeAclReader([SidecarAclReader(), PrefixAclReader()])
    assert reader.groups_for(str(f)) == ["PROJECT_PHOENIX"]   # echte ACL gewinnt


def test_s3_sidecar_fail_closed():
    boto3 = pytest.importorskip("boto3")
    moto = pytest.importorskip("moto")
    from moto import mock_aws
    from connectors.s3 import S3Connector, get_s3_acl_reader

    @mock_aws
    def run():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="corpbucket")
        s3.put_object(Bucket="corpbucket", Key="d/secret.txt", Body=b"geheim")
        s3.put_object(Bucket="corpbucket", Key="d/secret.txt.acl.json", Body=b"{ kaputt ")
        conn = S3Connector(bucket="corpbucket", prefix="d/",
                           acl_reader=get_s3_acl_reader("sidecar"), client=s3)
        docs = {d.metadata["file_name"]: d for d in conn.iter_documents()}
        # defekter Sidecar + reiner Sidecar-Reader -> keine Gruppe -> fuer niemanden sichtbar
        assert docs["secret.txt"].acl_groups == []
    run()
