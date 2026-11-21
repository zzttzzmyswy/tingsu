import { realpathSync } from 'node:fs';
import { isAbsolute, relative, resolve, sep } from 'node:path';
export const name = 'tingsu-meeting-tools';
export const inject = ['agents', 'tools', 'workingDirectory'];
export function apply(ctx) {
  const allowed = new Set(["mcp__tingsu__read_transcript", "mcp__tingsu__list_topic_meetings", "mcp__tingsu__read_topic_history", "mcp__tingsu__list_notes", "mcp__tingsu__create_note", "mcp__tingsu__update_note", "mcp__tingsu__delete_note", "mcp__tingsu__list_topic_notes", "mcp__tingsu__create_topic_note", "mcp__tingsu__share_note_to_topic", "mcp__tingsu__update_topic_note", "mcp__tingsu__set_topic_note_status", "mcp__tingsu__delete_topic_note", "mcp__tingsu__load_tools"]);
  const root = realpathSync(process.cwd());
  const paths = {read: 'file_path', read_image: 'file_path', glob: 'path', grep: 'path',
                 office_render: 'input', office_convert: 'input', office_recalculate: 'input'};
  const skills = new Set(['office', 'office-docx', 'office-xlsx', 'office-pptx']);
  ctx.on('agent/created', async ({agent, signal}) => {
    await ctx.workingDirectory.set(agent, root, signal);
    agent.ctx.tools.restrict({allow: [...Object.keys(paths), 'skill'].filter(name => ctx.tools.get(name))});
    const outputs = new Set();
    // Conversions return an Office Kit-owned temporary file outside the workspace.
    // Grant the producing session access to that exact output for follow-up analysis.
    agent.ctx.on('tools/result', (exec, result) => {
      if (exec.agent === agent && !result.isError && ['office_convert', 'office_recalculate'].includes(exec.name))
        outputs.add(realpathSync(result.value.file));
    });
    agent.ctx.tools.guard(call => {
      if (allowed.has(call.name)) return;
      if (call.name === 'skill' && skills.has(call.arguments?.name)) return;
      if (!Object.hasOwn(paths, call.name)) return 'Meeting tools only';
      const arg = call.arguments?.[paths[call.name]] ?? '.';
      try {
        // ripgrep receives the original path. A symlink before ".." can resolve
        // differently from path.resolve() when opened by a subprocess.
        if (arg.replaceAll('\\', '/').split('/').includes('..')) return 'Use a path within this workspace';
        const target = realpathSync(resolve(root, arg));
        const path = relative(root, target);
        if (relative(root, ctx.workingDirectory.get(agent.session)) === ''
            && (outputs.has(target) || (path !== '..' && !path.startsWith('..' + sep) && !isAbsolute(path)))) return;
      } catch { /* A missing path or a dangling symlink cannot be read. */ }
      return 'Choose an existing path in the meeting workspace';
    });
  }, {prepend: true});
  ctx.provide('tingsuMeetingTools', {ready: true});
}
