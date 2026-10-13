import asyncio
from pathlib import Path

import ezdxf
import pytest


def test_four_tools_are_registered(monkeypatch):
    from cad2gis.agent_mcp import create_server, get_capabilities
    monkeypatch.delenv('CAD2GIS_CAD_BUNDLE_COMMAND', raising=False)
    names = {t.name for t in asyncio.run(create_server().list_tools())}
    assert {'get_cad_bundle_status', 'prepare_cad_bundle_workspace',
            'list_cad_bundle_tools', 'call_cad_bundle_tool'} <= names
    assert get_capabilities()['optional_cad_bundle']['status'] == 'not_installed'


def test_missing_bundle_keeps_core_mcp_alive(monkeypatch):
    from cad2gis.agent_mcp import create_server
    monkeypatch.setenv('CAD2GIS_CAD_BUNDLE_COMMAND', '/nonexistent/cad-executable')
    server = create_server()
    async def exercise():
        response = await server.call_tool('get_cad_bundle_status', {})
        return response
    response = asyncio.run(exercise())
    assert 'invalid_install' in str(response)
    assert 'run_conversion' in {t.name for t in asyncio.run(server.list_tools())}


def test_bundle_workspace_obeys_project_roots(tmp_path, monkeypatch):
    from cad2gis.agent_cad import prepare_cad_bundle_workspace
    from cad2gis.agent_mcp import MCPServiceError
    allowed = tmp_path / 'allowed'
    allowed.mkdir()
    source = tmp_path / 'outside.dxf'
    ezdxf.new().saveas(source)
    monkeypatch.setenv('CAD2GIS_PROJECT_ROOT', str(allowed))
    monkeypatch.delenv('CAD2GIS_PROJECT_ROOTS', raising=False)
    monkeypatch.delenv('CLAUDE_PROJECT_DIR', raising=False)
    with pytest.raises(MCPServiceError, match='outside'):
        prepare_cad_bundle_workspace(str(source), str(allowed / 'workspace'))
    source = allowed / 'source.dxf'
    ezdxf.new().saveas(source)
    with pytest.raises(MCPServiceError, match='outside'):
        prepare_cad_bundle_workspace(str(source), str(tmp_path / 'escaped'))
    result = prepare_cad_bundle_workspace(str(source), str(allowed / 'workspace'))
    assert Path(result['workspace_id']).is_relative_to(allowed)
