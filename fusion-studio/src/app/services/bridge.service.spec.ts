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

  it('should call fetch on getProjectStatus', async () => {
    spyOn(window, 'fetch').and.returnValue(
      Promise.resolve(new Response(JSON.stringify({ success: true, data: { opened: true } })))
    );

    const res = await service.getProjectStatus();
    expect(window.fetch).toHaveBeenCalled();
    expect(res.success).toBeTrue();
    expect(res.data.opened).toBeTrue();
  });

  it('should call fetch on listFiles', async () => {
    spyOn(window, 'fetch').and.returnValue(
      Promise.resolve(new Response(JSON.stringify({ success: true, data: { files: [{ name: 'src' }] } })))
    );

    const res = await service.listFiles('src');
    expect(window.fetch).toHaveBeenCalled();
    expect(res.success).toBeTrue();
    expect(res.data.files.length).toBe(1);
  });

  it('should call fetch on startTask', async () => {
    spyOn(window, 'fetch').and.returnValue(
      Promise.resolve(new Response(JSON.stringify({ success: true, data: { status: 'started' } })))
    );

    const res = await service.startTask('Add input validation');
    expect(window.fetch).toHaveBeenCalled();
    expect(res.success).toBeTrue();
  });

  it('should call fetch on approveTask and rejectTask', async () => {
    const fetchSpy = spyOn(window, 'fetch').and.returnValue(
      Promise.resolve(new Response(JSON.stringify({ success: true, data: { promoted: true } })))
    );

    const approveRes = await service.approveTask('task-1');
    expect(approveRes.success).toBeTrue();
    expect(fetchSpy).toHaveBeenCalled();

    fetchSpy.and.returnValue(
      Promise.resolve(new Response(JSON.stringify({ success: true, data: { discarded: true } })))
    );

    const rejectRes = await service.rejectTask('task-1');
    expect(rejectRes.success).toBeTrue();
  });
});
