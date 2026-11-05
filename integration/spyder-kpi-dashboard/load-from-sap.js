// --- Load from SAP (live): paste into index.html's main <script>, next to handleFiles. ---
// Fetches the KPI report (required) and the supply / demand snapshot (optional) from /api/sap/* and loads them
// exactly like uploaded exports: same parsing, KPIs, notes, Data check, at-risk history and shared report.
// A Clear To Build workbook or Logican sheet already loaded is kept; a manually uploaded SAP export is replaced.
// Button: <button type="button" class="btn" onclick="loadFromSap()" data-tip="Read the KPI data straight from SAP now.">Load from SAP</button>
async function loadFromSap({refresh=false}={}){
  const hadReport=document.getElementById('results-screen').style.display==='block';
  showLoading('Reading live SAP data…');
  const get=async(name,required)=>{
    let res;
    try{res=await fetch(`/api/sap/${name}${refresh?'?refresh=1':''}`,{credentials:'same-origin',cache:'no-store'});}
    catch(err){if(required)throw new Error('The site could not be reached.');return null;}
    if(!res.ok){
      if(!required)return null;
      let msg=`SAP ${name}: HTTP ${res.status}`;
      try{msg=(await res.json()).error||msg;}catch(err){}
      throw new Error(msg);
    }
    const text=await res.text();
    const m=(res.headers.get('content-disposition')||'').match(/filename="?([^";]+)"?/);
    return new File([text],m?m[1]:`SAP_live_${name}.txt`,{type:'text/plain',lastModified:Date.now()});
  };
  try{
    const [kpi,sd]=await Promise.all([get('kpi',true),get('supply-demand',false)]);
    // Keep the non-SAP files on hand (Clear To Build, Logican sheet) so their pages stay filled.
    const keep=(loadedFiles||[]).filter(f=>{const r=fileReadCache.get(f);const rows=r&&r.rows;return !!rows&&(!!rows.cmSheet||(rows.length>0&&isBuildRiskRow(rows[0])));});
    await handleFiles([kpi,...(sd?[sd]:[]),...keep]);
  }catch(err){
    const message=`Load from SAP: ${err&&err.message||'failed'}`;
    if(hadReport){showResults();alert(message);}
    else{showUploadError(message);showUpload();}
  }
}
