local source = assert(arg[1], "Pass the actual Versions.lua path")
local cases = {
  {name="Forever extra string return", returns={"1.60.1","70205","Oct 2 2026",16001,"Beta"}, gameType="camelot"},
  {name="mainline four returns", returns={"12.0.1","12345","date",120100}, gameType="standard"},
  {name="numeric-string interface", returns={"1.60.1","70205","date","16001","Beta","x64"}, gameType="camelot"},
  {name="missing interface fallback", returns={"1.60.1","70205","date"}, gameType="camelot"},
}
for _, case in ipairs(cases) do
  local bridge = {S={}, C={GuidType={}, unitPlayer="player"}}
  local environment = setmetatable({
    GetBuildInfo=function() return unpack(case.returns) end,
    GetAddOnMetadata=function(addon,key)
      assert(addon=="MasterOfAgentsBridge" and key=="X-MasterOfAgents-GameType")
      return case.gameType
    end,
    WOW_PROJECT_ID=11, WOW_PROJECT_MAINLINE=1, WOW_PROJECT_CLASSIC=2,
    WOW_PROJECT_BURNING_CRUSADE_CLASSIC=5, WOW_PROJECT_WRATH_CLASSIC=11,
    WOW_PROJECT_CATACLYSM_CLASSIC=14,
    GetSpellInfo=function() return "Attack",nil,132152,0,0,5,6603 end,
    GetSpellPowerCost=function() return {} end,
    C_Container={},
    C_GossipInfo={GetOptions=function() return {} end},
    C_FriendList={},
    C_Map={GetBestMapForUnit=function() return 1 end, GetPlayerMapPosition=function() return nil end},
    CreateFrame=function() return {} end,
    bit={band=function() return 0 end},
  }, {__index=_G})
  environment._G=environment
  local chunk=assert(loadfile(source))
  setfenv(chunk,environment)
  local ok, message=pcall(chunk,"MasterOfAgentsBridge",{bridge})
  assert(ok,case.name..": "..tostring(message))
  assert(bridge.GameType==case.gameType,case.name..": wrong game type")
  assert(bridge.IsMainlineFamily(),case.name..": wrong API family")
  assert(bridge.ClientVersion==1,case.name..": wrong bridge protocol version")
  assert(bridge.IsForever()==(case.gameType=="camelot"),case.name..": wrong Forever detection")
  assert(bridge.GetSpellCastTime(6603)==0,case.name..": spell wrapper failed")
  print("PASS "..case.name)
end
