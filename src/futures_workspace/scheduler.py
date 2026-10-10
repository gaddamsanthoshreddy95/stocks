"""Compatibility entry point: Futures Trading maintenance is user initiated."""

class WorkspaceScheduler:
    def __init__(self,workspace):
        self.workspace=workspace

    def tick(self,now=None,progress=None):
        self.workspace.check_enabled()
        return {'status':'MANUAL_MODE','reason':'Automatic Futures Trading jobs are disabled. Use rotate, recheck or refresh-universe explicitly.'}
