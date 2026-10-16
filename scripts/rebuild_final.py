"""Reproduce the frozen 2026-08-29 final release from Git plus private inputs.

The input ZIP contains an original ROM and edited assets. Keep it private.
This command verifies the frozen release; it is not a new translation builder.
"""
from __future__ import annotations
import argparse,hashlib,json,subprocess,sys,zipfile
from pathlib import Path,PurePosixPath

ROOT=Path(__file__).resolve().parents[1]


def sha(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--private-inputs',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args()
    archive=args.private_inputs.resolve();output=args.output_dir.resolve()
    if output.exists():raise SystemExit('Use a new output directory; existing outputs are never overwritten.')
    expected=json.loads((ROOT/'docs/rebuild/PRIVATE_INPUTS_v44.json').read_text(encoding='utf-8'))
    translated=ROOT/'workspace/text/translation/another_code_full_text_v1_ko.csv'
    if sha(translated)!=expected['translationSHA256']:
        raise SystemExit('Translation differs from final v44. Regenerate audits/mapping for the new revision; do not reuse frozen reports.')
    with zipfile.ZipFile(archive) as z:
        actual=json.loads(z.read('_RESTORE_INPUTS_MANIFEST.json'))
        if actual!=expected:raise SystemExit('Private input manifest differs from the pinned final v44 manifest.')
        if set(z.namelist())!={r['path'] for r in expected['files']}|{'_RESTORE_INPUTS_MANIFEST.json'}:
            raise SystemExit('Unexpected or missing archive members.')
        payloads=[]
        for row in expected['files']:
            relative=PurePosixPath(row['path'])
            if relative.is_absolute() or '..' in relative.parts or '\\' in row['path'] or ':' in row['path']:
                raise SystemExit('Unsafe private input path.')
            data=z.read(row['path'])
            if len(data)!=row['bytes'] or hashlib.sha256(data).hexdigest()!=row['sha256']:
                raise SystemExit('Private input hash mismatch: '+row['path'])
            payloads.append((row['path'],data))
    inputs=output/'private_inputs';inputs.mkdir(parents=True)
    for relative,data in payloads:
        target=inputs/relative;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
    scripts=ROOT/'workspace/scripts'
    def run(name,arguments):
        log=output/(name+'.log')
        with log.open('w',encoding='utf-8') as stream:
            result=subprocess.run([sys.executable,'-X','utf8','-B',str(scripts/(name+'.py')),*map(str,arguments)],cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT)
        if result.returncode:raise SystemExit(f'{name} failed; see {log}')
    source=inputs/'original/Another Code 2tsu no Kioku.nds'
    text_rom=output/'another_code_text_v44.nds';text_report=output/'text_build.json'
    run('build_full_translation_qa',[
        '--source-rom',source,'--translated-csv',translated,
        '--mapping',ROOT/'workspace/config/hangul_mapping_full_candidate_20260829_v44.json',
        '--font-png',inputs/'inputs/font.png','--font-config',ROOT/'workspace/config/font_source.json',
        '--script-report',inputs/'workspace/text/source/script_candidates.json',
        '--aux-report',inputs/'workspace/text/source/auxiliary_candidates.json',
        '--reference-report',inputs/'workspace/analysis/text_references.json',
        '--translation-audit',inputs/'workspace/analysis/translation_csv_ko_audit_20260829_v44.json',
        '--capacity-audit',inputs/'workspace/analysis/translation_relocation_capacity_20260829_v44.json',
        '--pack-audit',inputs/'workspace/analysis/pack_resource_nonzero_audit_20260826_r2.json',
        '--runtime-label-overrides',ROOT/'workspace/config/runtime_label_overrides_v35.json',
        '--output-rom',text_rom,'--report',text_report])
    text_hash=sha(text_rom)
    if text_hash!=expected['expectedTextSHA256']:raise SystemExit('Text ROM differs from the frozen v44 result.')
    print('TEXT PASS',text_hash,flush=True)
    final_rom=output/'AnotherCode_KR_Final_20260829.nds'
    run('build_graphics_pack_qa',[
        '--source-rom',source,'--source-config',ROOT/'workspace/config/source_rom.json',
        '--base-rom',text_rom,'--base-report',text_report,
        '--pack-root',ROOT/'workspace/graphics/pack_v2',
        '--canvas-config',ROOT/'workspace/config/graphics_edit_transforms_v39_cumulative.csv',
        '--translated-dir',inputs/'workspace/graphics/translate_final_v39_cumulative/indexed',
        '--output-rom',final_rom,'--report',output/'graphics_build.json'])
    final_hash=sha(final_rom)
    if final_hash!=expected['expectedFinalSHA256']:raise SystemExit('Final ROM differs from the published release.')
    result={'status':'PASS','inputArchiveSHA256':sha(archive),'privateFilesVerified':len(expected['files']),
            'sourceSHA256':sha(source),'textSHA256':text_hash,'finalSHA256':final_hash,
            'finalBytes':final_rom.stat().st_size,'output':str(final_rom),'runtimeVerification':'not performed by this command'}
    (output/'REBUILD_RESULT.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('FINAL PASS',final_hash,flush=True)


if __name__=='__main__':main()
