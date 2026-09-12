import { TestBed } from '@angular/core/testing';
import { BridgeService } from './bridge.service';

describe('BridgeService', () => {
  let service: BridgeService;

  beforeEach(() => {
    TestBed.configureTestingModule({});
    service = TestBed.inject(BridgeService);
  });

  it('should be created', () => {
    expect(service).toBeTruthy();
  });

  it('should return mock project status in dev fallback mode', async () => {
    const res = await service.getProjectStatus();
    expect(res.success).toBeTrue();
    expect(res.data.opened).toBeTrue();
    expect(res.data.version).toBe('0.12.0');
    expect(res.data.optimization_mode).toBe('BALANCED');
  });

  it('should return mock provider status in dev fallback mode', async () => {
    const res = await service.getProviderStatus();
    expect(res.success).toBeTrue();
    expect(res.data.providers.length).toBeGreaterThan(0);
    expect(res.data.providers[0].name).toContain('Codex');
  });

  it('should list files in dev fallback mode', async () => {
    const res = await service.listFiles();
    expect(res.success).toBeTrue();
    expect(res.data.files.length).toBeGreaterThan(0);
    const names = res.data.files.map((f: any) => f.name);
    expect(names).toContain('src');
    expect(names).toContain('tests');
  });

  it('should handle startTask and emit events in dev fallback mode', async () => {
    const events: any[] = [];
    service.events$.subscribe((e) => events.push(e));

    const res = await service.startTask('Add input validation');
    expect(res.success).toBeTrue();
    expect(res.data.task_id).toBe('task-demo');
  });

  it('should handle approveTask and rejectTask', async () => {
    const approveRes = await service.approveTask('task-1');
    expect(approveRes.success).toBeTrue();
    expect(approveRes.data.promoted).toBeTrue();

    const rejectRes = await service.rejectTask('task-1');
    expect(rejectRes.success).toBeTrue();
    expect(rejectRes.data.discarded).toBeTrue();
  });
});
